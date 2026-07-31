//! Canonical Windows process-token measurement shared by both sides of the
//! protected child startup boundary.

#![cfg(windows)]

use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    cmp::Ordering as CmpOrdering,
    fmt,
    mem::{size_of, zeroed},
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle},
    ptr::{self, null_mut},
    sync::atomic::{compiler_fence, Ordering},
};
use windows_sys::Win32::{
    Foundation::{
        GetHandleInformation, GetLastError, LocalFree, ERROR_BAD_LENGTH, ERROR_INSUFFICIENT_BUFFER,
        ERROR_NO_TOKEN, HANDLE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE,
    },
    Security::{
        Authorization::{ConvertSidToStringSidW, ConvertStringSidToSidW},
        GetLengthSid, GetTokenInformation, IsTokenRestricted, IsValidSid, LookupPrivilegeNameW,
        TokenAppContainerSid, TokenCapabilities, TokenElevation, TokenElevationType,
        TokenElevationTypeDefault, TokenGroups, TokenHasRestrictions, TokenIntegrityLevel,
        TokenIsAppContainer, TokenMandatoryPolicy, TokenPrimary, TokenPrivileges,
        TokenRestrictedSids, TokenSandBoxInert, TokenSessionId, TokenType, TokenUIAccess,
        TokenUser, TokenVirtualizationAllowed, TokenVirtualizationEnabled, LUID_AND_ATTRIBUTES,
        PSID, SE_PRIVILEGE_ENABLED, SE_PRIVILEGE_ENABLED_BY_DEFAULT, SE_PRIVILEGE_USED_FOR_ACCESS,
        SID_AND_ATTRIBUTES, TOKEN_APPCONTAINER_INFORMATION, TOKEN_ELEVATION, TOKEN_ELEVATION_TYPE,
        TOKEN_GROUPS, TOKEN_MANDATORY_LABEL, TOKEN_MANDATORY_POLICY,
        TOKEN_MANDATORY_POLICY_NO_WRITE_UP, TOKEN_PRIVILEGES, TOKEN_QUERY, TOKEN_TYPE, TOKEN_USER,
    },
    System::{
        SystemServices::{
            SE_GROUP_ENABLED, SE_GROUP_ENABLED_BY_DEFAULT, SE_GROUP_INTEGRITY,
            SE_GROUP_INTEGRITY_ENABLED, SE_GROUP_OWNER,
        },
        Threading::{OpenProcessToken, OpenThreadToken},
    },
};

#[cfg(test)]
use windows_sys::Win32::{
    Security::{ImpersonateSelf, RevertToSelf, SecurityImpersonation},
    System::Threading::GetCurrentThread,
};

pub(crate) type ProcessTokenDigest = [u8; 32];

const PROCESS_TOKEN_OBSERVATION_DOMAIN: &[u8] = b"vrcforge-process-token-observation-v2\0";
const MAX_TOKEN_BYTES: u32 = 256 * 1024;
const MAX_TOKEN_GROUPS: usize = 1024;
const MAX_TOKEN_PRIVILEGES: usize = 256;
const MAX_PRIVILEGE_NAME_UTF16: u32 = 128;
const LOCAL_SYSTEM_SID: &str = "S-1-5-18";
const BUILTIN_ADMINISTRATORS_SID: &str = "S-1-5-32-544";
const MEDIUM_INTEGRITY_SID: &str = "S-1-16-8192";
const SYSTEM_INTEGRITY_SID: &str = "S-1-16-16384";
const AUTHORITY_SERVICE_SID: &str = "S-1-5-80-627086344-872206109-3199044541-2745001037-75066892";
const CHANGE_NOTIFY_PRIVILEGE: &str = "sechangenotifyprivilege";
pub(crate) const RESTRICTED_RUNNER_PRIMARY_TOKEN_ACQUISITION_BLOCKER: &str =
    "authority_runner_primary_token_machine_readback_not_connected";
const AUTHORITY_PRIVILEGES: [&str; 4] = [
    "seassignprimarytokenprivilege",
    "seincreasequotaprivilege",
    "setcbprivilege",
    CHANGE_NOTIFY_PRIVILEGE,
];
const SERVICE_SID_ENABLED_ATTRIBUTES: u32 =
    (SE_GROUP_ENABLED_BY_DEFAULT | SE_GROUP_ENABLED | SE_GROUP_OWNER) as u32;
const INTEGRITY_ATTRIBUTES: u32 = (SE_GROUP_INTEGRITY | SE_GROUP_INTEGRITY_ENABLED) as u32;
const PRIVILEGE_CAPABILITY_MASK: u32 =
    (SE_PRIVILEGE_ENABLED | SE_PRIVILEGE_ENABLED_BY_DEFAULT) as u32;
const PRIVILEGE_OBSERVATION_MASK: u32 = PRIVILEGE_CAPABILITY_MASK | SE_PRIVILEGE_USED_FOR_ACCESS;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ProcessTokenPolicy {
    DedicatedRestrictedRunner,
    RestrictedAuthority,
}

pub(crate) struct ExpectedRestrictedRunnerSid(Vec<u8>);

/// Opaque owner of a primary token that a future privileged account reader has
/// already measured against the exact dedicated-runner policy. The normal
/// build intentionally has no constructor from a raw handle or SID string.
pub(crate) struct VerifiedRestrictedRunnerPrimaryTokenCapability {
    primary_token: OwnedHandle,
    expected_runner_sid: ExpectedRestrictedRunnerSid,
    primary_token_digest: ProcessTokenDigest,
}

impl fmt::Debug for VerifiedRestrictedRunnerPrimaryTokenCapability {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VerifiedRestrictedRunnerPrimaryTokenCapability(<held-and-redacted>)")
    }
}

impl ExpectedRestrictedRunnerSid {
    // Constructed only by the service-side launcher. This shared source is
    // also compiled directly into both child test crates.
    #[cfg_attr(test, allow(dead_code))]
    pub(crate) fn from_canonical_text(value: &str) -> Result<Self, ProcessTokenMeasurementError> {
        if value.is_empty() || value.chars().any(|character| character == '\0') {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_expected_runner_sid_invalid",
            ));
        }
        let expected_sid_error =
            || ProcessTokenMeasurementError::new("child_handshake_expected_runner_sid_invalid");
        let owned = OwnedLocalSid::from_text(value).map_err(|_| expected_sid_error())?;
        let canonical = canonical_sid_text(owned.0).map_err(|_| expected_sid_error())?;
        if canonical != value {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_expected_runner_sid_invalid",
            ));
        }
        let length = unsafe { GetLengthSid(owned.0) } as usize;
        if length == 0 || length > 68 {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_expected_runner_sid_invalid",
            ));
        }
        let sid = unsafe { std::slice::from_raw_parts(owned.0.cast::<u8>(), length) }.to_vec();
        Ok(Self(sid))
    }

    fn as_bytes(&self) -> &[u8] {
        &self.0
    }

    pub(crate) fn matches_canonical_text(&self, value: &str) -> bool {
        Self::from_canonical_text(value)
            .is_ok_and(|expected| expected.as_bytes() == self.as_bytes())
    }
}

impl VerifiedRestrictedRunnerPrimaryTokenCapability {
    /// Machine account logon/token acquisition is a privileged action-time
    /// step and remains closed until its provisioning readback is wired.
    pub(crate) fn from_production_machine_readback() -> Result<Self, ProcessTokenMeasurementError> {
        Err(ProcessTokenMeasurementError::new(
            RESTRICTED_RUNNER_PRIMARY_TOKEN_ACQUISITION_BLOCKER,
        ))
    }

    pub(crate) fn verifies_account_sid(&self, canonical_account_sid: &str) -> bool {
        self.expected_runner_sid
            .matches_canonical_text(canonical_account_sid)
    }

    pub(crate) fn into_verified_parts(
        self,
    ) -> Result<
        (OwnedHandle, ExpectedRestrictedRunnerSid, ProcessTokenDigest),
        ProcessTokenMeasurementError,
    > {
        let mut flags = 0u32;
        if self.primary_token_digest.iter().all(|value| *value == 0)
            || unsafe {
                GetHandleInformation(self.primary_token.as_raw_handle().cast(), &mut flags)
            } == 0
            || flags & HANDLE_FLAG_INHERIT != 0
        {
            return Err(ProcessTokenMeasurementError::new(
                "authority_runner_primary_token_capability_invalid",
            ));
        }
        Ok((
            self.primary_token,
            self.expected_runner_sid,
            self.primary_token_digest,
        ))
    }

    #[cfg(test)]
    pub(crate) fn exact_test_fixture(
        primary_token: OwnedHandle,
        canonical_account_sid: &str,
        primary_token_digest: ProcessTokenDigest,
    ) -> Self {
        let expected_runner_sid =
            ExpectedRestrictedRunnerSid::from_canonical_text(canonical_account_sid)
                .expect("runner token fixture SID must be canonical");
        let value = Self {
            primary_token,
            expected_runner_sid,
            primary_token_digest,
        };
        assert!(value.primary_token_digest.iter().any(|byte| *byte != 0));
        value
    }

    #[cfg(test)]
    pub(crate) fn with_token_digest_for_test(
        mut self,
        primary_token_digest: ProcessTokenDigest,
    ) -> Self {
        self.primary_token_digest = primary_token_digest;
        self
    }
}

impl Clone for ExpectedRestrictedRunnerSid {
    fn clone(&self) -> Self {
        Self(self.0.clone())
    }
}

impl fmt::Debug for ExpectedRestrictedRunnerSid {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ExpectedRestrictedRunnerSid(<redacted>)")
    }
}

impl Drop for ExpectedRestrictedRunnerSid {
    fn drop(&mut self) {
        volatile_zero(&mut self.0);
    }
}

impl ProcessTokenPolicy {
    const fn tag(self) -> u8 {
        match self {
            Self::DedicatedRestrictedRunner => 1,
            Self::RestrictedAuthority => 2,
        }
    }

    const fn policy_error(self) -> &'static str {
        match self {
            Self::DedicatedRestrictedRunner => "child_handshake_dedicated_runner_token_required",
            Self::RestrictedAuthority => "child_handshake_restricted_authority_token_required",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProcessTokenMeasurementError(&'static str);

impl ProcessTokenMeasurementError {
    fn new(code: &'static str) -> Self {
        Self(code)
    }

    pub(crate) const fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for ProcessTokenMeasurementError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for ProcessTokenMeasurementError {}

#[derive(Clone, Eq, PartialEq)]
struct ObservedTokenGroup {
    sid: Vec<u8>,
    attributes: u32,
}

impl Ord for ObservedTokenGroup {
    fn cmp(&self, other: &Self) -> CmpOrdering {
        self.sid
            .cmp(&other.sid)
            .then_with(|| self.attributes.cmp(&other.attributes))
    }
}

impl PartialOrd for ObservedTokenGroup {
    fn partial_cmp(&self, other: &Self) -> Option<CmpOrdering> {
        Some(self.cmp(other))
    }
}

impl Drop for ObservedTokenGroup {
    fn drop(&mut self) {
        volatile_zero(&mut self.sid);
    }
}

#[derive(Clone, Eq, PartialEq, Ord, PartialOrd)]
struct ObservedTokenPrivilege {
    name: String,
    attributes: u32,
}

#[derive(Clone)]
struct ProcessTokenObservation {
    token_type: TOKEN_TYPE,
    session_id: u32,
    restricted: bool,
    has_restrictions: u32,
    elevated: bool,
    elevation_type: TOKEN_ELEVATION_TYPE,
    user_sid: Vec<u8>,
    user_attributes: u32,
    integrity_sid: Vec<u8>,
    integrity_attributes: u32,
    mandatory_policy: u32,
    ui_access: u32,
    virtualization_allowed: u32,
    virtualization_enabled: u32,
    sandbox_inert: u32,
    is_app_container: u32,
    app_container_sid: Option<Vec<u8>>,
    groups: Vec<ObservedTokenGroup>,
    restricting_groups: Vec<ObservedTokenGroup>,
    capabilities: Vec<ObservedTokenGroup>,
    privileges: Vec<ObservedTokenPrivilege>,
}

impl Drop for ProcessTokenObservation {
    fn drop(&mut self) {
        volatile_zero(&mut self.user_sid);
        volatile_zero(&mut self.integrity_sid);
        if let Some(sid) = self.app_container_sid.as_mut() {
            volatile_zero(sid);
        }
    }
}

pub(crate) fn measure_process_token_digest(
    process: HANDLE,
    policy: ProcessTokenPolicy,
) -> Result<ProcessTokenDigest, ProcessTokenMeasurementError> {
    let observation = ProcessTokenObservation::from_process(process)?;
    observation.validate_and_digest(policy)
}

pub(crate) fn measure_expected_restricted_runner_token_digest(
    process: HANDLE,
    expected_runner_sid: &ExpectedRestrictedRunnerSid,
) -> Result<ProcessTokenDigest, ProcessTokenMeasurementError> {
    let observation = ProcessTokenObservation::from_process(process)?;
    observation.validate_and_digest_with_expected_runner(
        ProcessTokenPolicy::DedicatedRestrictedRunner,
        Some(expected_runner_sid.as_bytes()),
    )
}

pub(crate) fn measure_expected_restricted_runner_primary_token_digest(
    token: HANDLE,
    expected_runner_sid: &ExpectedRestrictedRunnerSid,
) -> Result<ProcessTokenDigest, ProcessTokenMeasurementError> {
    let observation = ProcessTokenObservation::from_token(token)?;
    observation.validate_and_digest_with_expected_runner(
        ProcessTokenPolicy::DedicatedRestrictedRunner,
        Some(expected_runner_sid.as_bytes()),
    )
}

pub(crate) fn require_thread_without_impersonation_token(
    thread: HANDLE,
) -> Result<(), ProcessTokenMeasurementError> {
    let mut raw_token: HANDLE = null_mut();
    let opened = unsafe { OpenThreadToken(thread, TOKEN_QUERY, 1, &mut raw_token) };
    if opened != 0 {
        if !raw_token.is_null() && raw_token != INVALID_HANDLE_VALUE {
            drop(unsafe { OwnedHandle::from_raw_handle(raw_token as RawHandle) });
        }
        return Err(ProcessTokenMeasurementError::new(
            "protected_child_thread_impersonation_token_forbidden",
        ));
    }
    if !raw_token.is_null() && raw_token != INVALID_HANDLE_VALUE {
        drop(unsafe { OwnedHandle::from_raw_handle(raw_token as RawHandle) });
        return Err(ProcessTokenMeasurementError::new(
            "protected_child_thread_token_probe_failed",
        ));
    }
    if unsafe { GetLastError() } != ERROR_NO_TOKEN {
        return Err(ProcessTokenMeasurementError::new(
            "protected_child_thread_token_probe_failed",
        ));
    }
    Ok(())
}

impl ProcessTokenObservation {
    fn from_process(process: HANDLE) -> Result<Self, ProcessTokenMeasurementError> {
        let mut raw_token: HANDLE = null_mut();
        if unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut raw_token) } == 0
            || raw_token.is_null()
            || raw_token == INVALID_HANDLE_VALUE
        {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_unavailable",
            ));
        }
        let token = unsafe { OwnedHandle::from_raw_handle(raw_token as RawHandle) };
        Self::from_token(token.as_raw_handle().cast())
    }

    fn from_token(token: HANDLE) -> Result<Self, ProcessTokenMeasurementError> {
        if token.is_null() || token == INVALID_HANDLE_VALUE {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_unavailable",
            ));
        }
        let user_buffer = query_token_buffer(token, TokenUser)?;
        if user_buffer.byte_len() < size_of::<TOKEN_USER>() {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_invalid",
            ));
        }
        let user = unsafe { &*(user_buffer.as_ptr().cast::<TOKEN_USER>()) };

        let integrity_buffer = query_token_buffer(token, TokenIntegrityLevel)?;
        if integrity_buffer.byte_len() < size_of::<TOKEN_MANDATORY_LABEL>() {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_invalid",
            ));
        }
        let integrity = unsafe { &*(integrity_buffer.as_ptr().cast::<TOKEN_MANDATORY_LABEL>()) };
        let is_app_container = query_token_boolean(token, TokenIsAppContainer)?;
        let app_container_sid = if is_app_container == 0 {
            None
        } else {
            let app_container_buffer = query_token_buffer(token, TokenAppContainerSid)?;
            if app_container_buffer.byte_len() != size_of::<TOKEN_APPCONTAINER_INFORMATION>() {
                return Err(ProcessTokenMeasurementError::new(
                    "child_handshake_process_token_invalid",
                ));
            }
            let app_container = unsafe {
                &*(app_container_buffer
                    .as_ptr()
                    .cast::<TOKEN_APPCONTAINER_INFORMATION>())
            };
            if app_container.TokenAppContainer.is_null() {
                return Err(ProcessTokenMeasurementError::new(
                    "child_handshake_process_token_invalid",
                ));
            }
            Some(sid_bytes_in_buffer(
                &app_container_buffer,
                app_container.TokenAppContainer,
            )?)
        };
        let elevation: TOKEN_ELEVATION = query_token_fixed(token, TokenElevation)?;
        let mandatory: TOKEN_MANDATORY_POLICY = query_token_fixed(token, TokenMandatoryPolicy)?;
        let restricted = unsafe { IsTokenRestricted(token) } != 0;
        Ok(Self {
            token_type: query_token_fixed(token, TokenType)?,
            session_id: query_token_fixed(token, TokenSessionId)?,
            restricted,
            has_restrictions: query_token_boolean(token, TokenHasRestrictions)?,
            elevated: elevation.TokenIsElevated != 0,
            elevation_type: query_token_fixed(token, TokenElevationType)?,
            user_sid: sid_bytes_in_buffer(&user_buffer, user.User.Sid)?,
            user_attributes: user.User.Attributes,
            integrity_sid: sid_bytes_in_buffer(&integrity_buffer, integrity.Label.Sid)?,
            integrity_attributes: integrity.Label.Attributes,
            mandatory_policy: mandatory.Policy,
            ui_access: query_token_boolean(token, TokenUIAccess)?,
            virtualization_allowed: query_token_boolean(token, TokenVirtualizationAllowed)?,
            virtualization_enabled: query_token_boolean(token, TokenVirtualizationEnabled)?,
            sandbox_inert: query_token_boolean(token, TokenSandBoxInert)?,
            is_app_container,
            app_container_sid,
            groups: query_token_groups(token, TokenGroups, false)?,
            restricting_groups: query_token_groups(token, TokenRestrictedSids, true)?,
            capabilities: query_token_groups(token, TokenCapabilities, true)?,
            privileges: query_token_privileges(token)?,
        })
    }

    fn validate_and_digest(
        &self,
        policy: ProcessTokenPolicy,
    ) -> Result<ProcessTokenDigest, ProcessTokenMeasurementError> {
        self.validate_and_digest_with_expected_runner(policy, None)
    }

    fn validate_and_digest_with_expected_runner(
        &self,
        policy: ProcessTokenPolicy,
        expected_runner_sid: Option<&[u8]>,
    ) -> Result<ProcessTokenDigest, ProcessTokenMeasurementError> {
        let policy_error = policy.policy_error();
        let local_system = sid_from_text(LOCAL_SYSTEM_SID)?;
        let administrators = sid_from_text(BUILTIN_ADMINISTRATORS_SID)?;
        let service = sid_from_text(AUTHORITY_SERVICE_SID)?;
        let expected_integrity = sid_from_text(match policy {
            ProcessTokenPolicy::DedicatedRestrictedRunner => MEDIUM_INTEGRITY_SID,
            ProcessTokenPolicy::RestrictedAuthority => SYSTEM_INTEGRITY_SID,
        })?;
        if self.token_type != TokenPrimary
            || self.session_id != 0
            || !self.restricted
            || self.has_restrictions != 1
            || self.elevation_type != TokenElevationTypeDefault
            || self.user_attributes != 0
            || self.integrity_sid != expected_integrity
            || self.integrity_attributes != INTEGRITY_ATTRIBUTES
            || self.mandatory_policy != TOKEN_MANDATORY_POLICY_NO_WRITE_UP
            || self.ui_access != 0
            || self.virtualization_allowed != 0
            || self.virtualization_enabled != 0
            || self.sandbox_inert != 0
            || self.is_app_container != 0
            || self.app_container_sid.is_some()
            || !self.capabilities.is_empty()
        {
            return Err(ProcessTokenMeasurementError::new(policy_error));
        }

        match policy {
            ProcessTokenPolicy::DedicatedRestrictedRunner => {
                if self.elevated
                    || self.user_sid == local_system
                    || self.user_sid == administrators
                    || is_service_sid(&self.user_sid)
                    || expected_runner_sid
                        .is_some_and(|expected| self.user_sid.as_slice() != expected)
                    || self
                        .groups
                        .iter()
                        .any(|group| group.sid == administrators || is_service_sid(&group.sid))
                    || self.restricting_groups.len() != 1
                    || self.restricting_groups[0].sid != self.user_sid
                    || self.restricting_groups[0].attributes != 0
                {
                    return Err(ProcessTokenMeasurementError::new(policy_error));
                }
                require_exact_privileges(&self.privileges, policy)?;
            }
            ProcessTokenPolicy::RestrictedAuthority => {
                if expected_runner_sid.is_some() {
                    return Err(ProcessTokenMeasurementError::new(policy_error));
                }
                let mut enabled_service = self.groups.iter().filter(|group| group.sid == service);
                let enabled_service = enabled_service.next();
                if !self.elevated
                    || self.user_sid != local_system
                    || enabled_service.is_none()
                    || enabled_service
                        .is_some_and(|group| group.attributes != SERVICE_SID_ENABLED_ATTRIBUTES)
                    || self
                        .groups
                        .iter()
                        .filter(|group| group.sid == service)
                        .count()
                        != 1
                    || self.restricting_groups.len() != 1
                    || self.restricting_groups[0].sid != service
                    || self.restricting_groups[0].attributes != 0
                {
                    return Err(ProcessTokenMeasurementError::new(policy_error));
                }
                require_exact_privileges(&self.privileges, policy)?;
            }
        }

        let mut digest = Sha256::new();
        digest.update(PROCESS_TOKEN_OBSERVATION_DOMAIN);
        digest.update([policy.tag()]);
        for value in [self.token_type, self.elevation_type] {
            digest.update(value.to_be_bytes());
        }
        digest.update(self.session_id.to_be_bytes());
        digest.update([u8::from(self.restricted), u8::from(self.elevated)]);
        for value in [
            self.has_restrictions,
            self.user_attributes,
            self.integrity_attributes,
            self.mandatory_policy,
            self.ui_access,
            self.virtualization_allowed,
            self.virtualization_enabled,
            self.sandbox_inert,
            self.is_app_container,
        ] {
            digest.update(value.to_be_bytes());
        }
        update_bytes(&mut digest, &self.user_sid)?;
        update_bytes(&mut digest, &self.integrity_sid)?;
        digest.update([u8::from(self.app_container_sid.is_some())]);
        if let Some(sid) = &self.app_container_sid {
            update_bytes(&mut digest, sid)?;
        }
        update_groups(&mut digest, &self.groups)?;
        update_groups(&mut digest, &self.restricting_groups)?;
        update_groups(&mut digest, &self.capabilities)?;
        digest.update((self.privileges.len() as u32).to_be_bytes());
        for privilege in &self.privileges {
            let name = privilege.name.as_bytes();
            let length = u16::try_from(name.len()).map_err(|_| {
                ProcessTokenMeasurementError::new(
                    "child_handshake_process_token_privileges_invalid",
                )
            })?;
            digest.update(length.to_be_bytes());
            digest.update(name);
            digest.update(privilege.attributes.to_be_bytes());
        }
        let value: ProcessTokenDigest = digest.finalize().into();
        if value.iter().all(|byte| *byte == 0) {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_observation_invalid",
            ));
        }
        Ok(value)
    }
}

fn require_exact_privileges(
    privileges: &[ObservedTokenPrivilege],
    policy: ProcessTokenPolicy,
) -> Result<(), ProcessTokenMeasurementError> {
    let expected = match policy {
        ProcessTokenPolicy::DedicatedRestrictedRunner => &[CHANGE_NOTIFY_PRIVILEGE][..],
        ProcessTokenPolicy::RestrictedAuthority => &AUTHORITY_PRIVILEGES,
    };
    if privileges.len() != expected.len()
        || !expected.iter().all(|name| {
            privileges.iter().any(|privilege| {
                privilege.name == *name
                    && privilege.attributes
                        == if *name == CHANGE_NOTIFY_PRIVILEGE {
                            PRIVILEGE_CAPABILITY_MASK
                        } else {
                            0
                        }
            })
        })
    {
        return Err(ProcessTokenMeasurementError::new(policy.policy_error()));
    }
    Ok(())
}

fn update_bytes(digest: &mut Sha256, bytes: &[u8]) -> Result<(), ProcessTokenMeasurementError> {
    let length = u32::try_from(bytes.len())
        .map_err(|_| ProcessTokenMeasurementError::new("child_handshake_process_token_invalid"))?;
    digest.update(length.to_be_bytes());
    digest.update(bytes);
    Ok(())
}

fn update_groups(
    digest: &mut Sha256,
    groups: &[ObservedTokenGroup],
) -> Result<(), ProcessTokenMeasurementError> {
    let count = u32::try_from(groups.len()).map_err(|_| {
        ProcessTokenMeasurementError::new("child_handshake_process_token_groups_invalid")
    })?;
    digest.update(count.to_be_bytes());
    for group in groups {
        update_bytes(digest, &group.sid)?;
        digest.update(group.attributes.to_be_bytes());
    }
    Ok(())
}

fn query_token_boolean(token: HANDLE, class: i32) -> Result<u32, ProcessTokenMeasurementError> {
    let buffer = query_token_buffer(token, class)?;
    let value = match buffer.byte_len() {
        1 => unsafe { *buffer.as_ptr().cast::<u8>() as u32 },
        4 => unsafe { *buffer.as_ptr().cast::<u32>() },
        _ => {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_invalid",
            ))
        }
    };
    Ok(u32::from(value != 0))
}

fn query_token_fixed<T: Copy>(
    token: HANDLE,
    class: i32,
) -> Result<T, ProcessTokenMeasurementError> {
    let buffer = query_token_buffer(token, class)?;
    if buffer.byte_len() != size_of::<T>() {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_invalid",
        ));
    }
    Ok(unsafe { *(buffer.as_ptr().cast::<T>()) })
}

struct SensitiveWordBuffer {
    words: Vec<usize>,
    byte_len: usize,
}

impl SensitiveWordBuffer {
    fn as_ptr(&self) -> *const usize {
        self.words.as_ptr()
    }

    fn byte_len(&self) -> usize {
        self.byte_len
    }

    fn contains(&self, pointer: *const u8, length: usize) -> bool {
        let start = self.words.as_ptr() as usize;
        let end = start.saturating_add(self.byte_len);
        let candidate = pointer as usize;
        candidate >= start
            && candidate
                .checked_add(length)
                .is_some_and(|value| value <= end)
    }
}

impl Drop for SensitiveWordBuffer {
    fn drop(&mut self) {
        volatile_zero(&mut self.words);
    }
}

fn query_token_buffer(
    token: HANDLE,
    class: i32,
) -> Result<SensitiveWordBuffer, ProcessTokenMeasurementError> {
    let mut required = 0u32;
    unsafe {
        GetTokenInformation(token, class, null_mut(), 0, &mut required);
    }
    let probe_error = unsafe { GetLastError() };
    if required == 0
        || required > MAX_TOKEN_BYTES
        || !matches!(probe_error, ERROR_INSUFFICIENT_BUFFER | ERROR_BAD_LENGTH)
    {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_unavailable",
        ));
    }
    let word_size = size_of::<usize>();
    let word_count = (required as usize)
        .checked_add(word_size - 1)
        .ok_or_else(|| {
            ProcessTokenMeasurementError::new("child_handshake_process_token_invalid")
        })?
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
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_unavailable",
        ));
    }
    Ok(SensitiveWordBuffer {
        words,
        byte_len: returned as usize,
    })
}

fn query_token_groups(
    token: HANDLE,
    class: i32,
    allow_empty: bool,
) -> Result<Vec<ObservedTokenGroup>, ProcessTokenMeasurementError> {
    let buffer = query_token_buffer(token, class)?;
    if buffer.byte_len() < size_of::<u32>() {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_groups_invalid",
        ));
    }
    let count = unsafe { *(buffer.as_ptr().cast::<u32>()) } as usize;
    if count > MAX_TOKEN_GROUPS || (!allow_empty && count == 0) {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_groups_invalid",
        ));
    }
    if count == 0 {
        return Ok(Vec::new());
    }
    if buffer.byte_len() < size_of::<TOKEN_GROUPS>() {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_groups_invalid",
        ));
    }
    let groups = unsafe { &*(buffer.as_ptr().cast::<TOKEN_GROUPS>()) };
    let offset = (ptr::addr_of!(groups.Groups) as usize)
        .checked_sub(groups as *const TOKEN_GROUPS as usize)
        .ok_or_else(|| {
            ProcessTokenMeasurementError::new("child_handshake_process_token_groups_invalid")
        })?;
    if buffer.byte_len() < offset {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_groups_invalid",
        ));
    }
    let required = offset
        .checked_add(
            count
                .checked_mul(size_of::<SID_AND_ATTRIBUTES>())
                .ok_or_else(|| {
                    ProcessTokenMeasurementError::new(
                        "child_handshake_process_token_groups_invalid",
                    )
                })?,
        )
        .ok_or_else(|| {
            ProcessTokenMeasurementError::new("child_handshake_process_token_groups_invalid")
        })?;
    if required > buffer.byte_len() {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_groups_invalid",
        ));
    }
    let entries = unsafe { std::slice::from_raw_parts(groups.Groups.as_ptr(), count) };
    let mut observed = entries
        .iter()
        .map(|entry| {
            Ok(ObservedTokenGroup {
                sid: sid_bytes_in_buffer(&buffer, entry.Sid)?,
                attributes: entry.Attributes,
            })
        })
        .collect::<Result<Vec<_>, ProcessTokenMeasurementError>>()?;
    observed.sort();
    if observed.windows(2).any(|pair| pair[0].sid == pair[1].sid) {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_groups_invalid",
        ));
    }
    Ok(observed)
}

fn query_token_privileges(
    token: HANDLE,
) -> Result<Vec<ObservedTokenPrivilege>, ProcessTokenMeasurementError> {
    let buffer = query_token_buffer(token, TokenPrivileges)?;
    if buffer.byte_len() < size_of::<TOKEN_PRIVILEGES>() {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_privileges_invalid",
        ));
    }
    let privileges = unsafe { &*(buffer.as_ptr().cast::<TOKEN_PRIVILEGES>()) };
    let count = privileges.PrivilegeCount as usize;
    if count == 0 || count > MAX_TOKEN_PRIVILEGES {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_privileges_invalid",
        ));
    }
    let offset = (ptr::addr_of!(privileges.Privileges) as usize)
        .checked_sub(privileges as *const TOKEN_PRIVILEGES as usize)
        .ok_or_else(|| {
            ProcessTokenMeasurementError::new("child_handshake_process_token_privileges_invalid")
        })?;
    let required = offset
        .checked_add(
            count
                .checked_mul(size_of::<LUID_AND_ATTRIBUTES>())
                .ok_or_else(|| {
                    ProcessTokenMeasurementError::new(
                        "child_handshake_process_token_privileges_invalid",
                    )
                })?,
        )
        .ok_or_else(|| {
            ProcessTokenMeasurementError::new("child_handshake_process_token_privileges_invalid")
        })?;
    if required > buffer.byte_len() {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_privileges_invalid",
        ));
    }
    let entries = unsafe { std::slice::from_raw_parts(privileges.Privileges.as_ptr(), count) };
    let mut observed = Vec::with_capacity(entries.len());
    for entry in entries {
        if entry.Attributes & !PRIVILEGE_OBSERVATION_MASK != 0 {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_privileges_invalid",
            ));
        }
        let name = lookup_privilege_name(&entry.Luid)?.to_ascii_lowercase();
        if !name.is_ascii() || name.is_empty() || name.len() > 128 {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_privileges_invalid",
            ));
        }
        observed.push(ObservedTokenPrivilege {
            name,
            attributes: entry.Attributes & PRIVILEGE_CAPABILITY_MASK,
        });
    }
    observed.sort();
    if observed.windows(2).any(|pair| pair[0].name == pair[1].name) {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_privileges_invalid",
        ));
    }
    Ok(observed)
}

fn lookup_privilege_name(
    luid: &windows_sys::Win32::Foundation::LUID,
) -> Result<String, ProcessTokenMeasurementError> {
    let mut required = 0u32;
    unsafe {
        LookupPrivilegeNameW(null_mut(), luid, null_mut(), &mut required);
    }
    if unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_privilege_name_unavailable",
        ));
    }
    let capacity = bounded_privilege_name_capacity(required)?;
    let mut words = vec![0u16; capacity];
    let mut length = capacity as u32;
    if unsafe { LookupPrivilegeNameW(null_mut(), luid, words.as_mut_ptr(), &mut length) } == 0
        || length == 0
        || length as usize >= capacity
    {
        volatile_zero(&mut words);
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_privilege_name_unavailable",
        ));
    }
    words.truncate(length as usize);
    if words.contains(&0) {
        volatile_zero(&mut words);
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_privileges_invalid",
        ));
    }
    let value = String::from_utf16(&words).map_err(|_| {
        ProcessTokenMeasurementError::new("child_handshake_process_token_privileges_invalid")
    });
    volatile_zero(&mut words);
    value
}

fn bounded_privilege_name_capacity(required: u32) -> Result<usize, ProcessTokenMeasurementError> {
    if required == 0 || required > MAX_PRIVILEGE_NAME_UTF16 {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_privileges_invalid",
        ));
    }
    usize::try_from(required)
        .ok()
        .and_then(|value| value.checked_add(1))
        .ok_or_else(|| {
            ProcessTokenMeasurementError::new("child_handshake_process_token_privileges_invalid")
        })
}

fn sid_bytes_in_buffer(
    buffer: &SensitiveWordBuffer,
    sid: PSID,
) -> Result<Vec<u8>, ProcessTokenMeasurementError> {
    let expected_length = bounded_embedded_sid_length(buffer, sid.cast())?;
    if unsafe { IsValidSid(sid) } == 0 {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_sid_invalid",
        ));
    }
    let length = unsafe { GetLengthSid(sid) } as usize;
    if length != expected_length {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_sid_invalid",
        ));
    }
    Ok(unsafe { std::slice::from_raw_parts(sid.cast::<u8>(), length) }.to_vec())
}

fn bounded_embedded_sid_length(
    buffer: &SensitiveWordBuffer,
    sid: *const u8,
) -> Result<usize, ProcessTokenMeasurementError> {
    const SID_HEADER_BYTES: usize = 8;
    const MAX_SID_BYTES: usize = 68;
    if sid.is_null() || !buffer.contains(sid, SID_HEADER_BYTES) {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_sid_invalid",
        ));
    }
    let sub_authority_count = unsafe { sid.add(1).read() } as usize;
    let length = sub_authority_count
        .checked_mul(size_of::<u32>())
        .and_then(|body| SID_HEADER_BYTES.checked_add(body))
        .ok_or_else(|| {
            ProcessTokenMeasurementError::new("child_handshake_process_token_sid_invalid")
        })?;
    if length > MAX_SID_BYTES || !buffer.contains(sid, length) {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_sid_invalid",
        ));
    }
    Ok(length)
}

struct OwnedLocalSid(PSID);

impl OwnedLocalSid {
    fn from_text(text: &str) -> Result<Self, ProcessTokenMeasurementError> {
        let mut words = text.encode_utf16().collect::<Vec<_>>();
        words.push(0);
        let mut sid = null_mut();
        let converted = unsafe { ConvertStringSidToSidW(words.as_ptr(), &mut sid) };
        volatile_zero(&mut words);
        if converted == 0 || sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
            if !sid.is_null() {
                unsafe {
                    LocalFree(sid.cast());
                }
            }
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_sid_invalid",
            ));
        }
        Ok(Self(sid))
    }
}

impl Drop for OwnedLocalSid {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0.cast());
            }
        }
    }
}

fn sid_from_text(text: &str) -> Result<Vec<u8>, ProcessTokenMeasurementError> {
    let sid = OwnedLocalSid::from_text(text)?;
    let length = unsafe { GetLengthSid(sid.0) } as usize;
    if length == 0 || length > 68 {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_sid_invalid",
        ));
    }
    Ok(unsafe { std::slice::from_raw_parts(sid.0.cast::<u8>(), length) }.to_vec())
}

fn canonical_sid_text(sid: PSID) -> Result<String, ProcessTokenMeasurementError> {
    const MAX_CANONICAL_SID_UTF16: usize = 192;
    let mut text = null_mut();
    if unsafe { ConvertSidToStringSidW(sid, &mut text) } == 0 || text.is_null() {
        return Err(ProcessTokenMeasurementError::new(
            "child_handshake_process_token_sid_invalid",
        ));
    }
    let result = (|| {
        let mut length = 0usize;
        while length <= MAX_CANONICAL_SID_UTF16 && unsafe { *text.add(length) } != 0 {
            length += 1;
        }
        if length == 0 || length > MAX_CANONICAL_SID_UTF16 {
            return Err(ProcessTokenMeasurementError::new(
                "child_handshake_process_token_sid_invalid",
            ));
        }
        String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) }).map_err(|_| {
            ProcessTokenMeasurementError::new("child_handshake_process_token_sid_invalid")
        })
    })();
    unsafe {
        LocalFree(text.cast());
    }
    result
}

fn is_service_sid(bytes: &[u8]) -> bool {
    if bytes.len() < 12 || bytes[0] != 1 {
        return false;
    }
    let sub_authority_count = bytes[1] as usize;
    if sub_authority_count == 0 || bytes.len() != 8 + sub_authority_count * 4 {
        return false;
    }
    let identifier_authority = u64::from_be_bytes([
        0, 0, bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
    ]);
    let first_sub_authority = u32::from_le_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]);
    identifier_authority == 5 && first_sub_authority == 80
}

fn volatile_zero<T>(values: &mut [T]) {
    for value in values {
        unsafe {
            ptr::write_volatile(value, zeroed());
        }
    }
    compiler_fence(Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use super::*;

    struct RevertImpersonation;

    impl Drop for RevertImpersonation {
        fn drop(&mut self) {
            assert_ne!(unsafe { RevertToSelf() }, 0);
        }
    }

    fn sensitive_buffer(bytes: &[u8]) -> SensitiveWordBuffer {
        let word_size = size_of::<usize>();
        let word_count = bytes.len().div_ceil(word_size);
        let mut buffer = SensitiveWordBuffer {
            words: vec![0usize; word_count.max(1)],
            byte_len: bytes.len(),
        };
        unsafe {
            std::slice::from_raw_parts_mut(buffer.words.as_mut_ptr().cast::<u8>(), bytes.len())
        }
        .copy_from_slice(bytes);
        buffer
    }

    fn privilege(name: &str, attributes: u32) -> ObservedTokenPrivilege {
        ObservedTokenPrivilege {
            name: name.to_string(),
            attributes,
        }
    }

    fn runner_observation() -> ProcessTokenObservation {
        ProcessTokenObservation {
            token_type: TokenPrimary,
            session_id: 0,
            restricted: true,
            has_restrictions: 1,
            elevated: false,
            elevation_type: TokenElevationTypeDefault,
            user_sid: sid_from_text("S-1-5-21-111-222-333-1001").unwrap(),
            user_attributes: 0,
            integrity_sid: sid_from_text(MEDIUM_INTEGRITY_SID).unwrap(),
            integrity_attributes: INTEGRITY_ATTRIBUTES,
            mandatory_policy: TOKEN_MANDATORY_POLICY_NO_WRITE_UP,
            ui_access: 0,
            virtualization_allowed: 0,
            virtualization_enabled: 0,
            sandbox_inert: 0,
            is_app_container: 0,
            app_container_sid: None,
            groups: vec![ObservedTokenGroup {
                sid: sid_from_text("S-1-5-32-545").unwrap(),
                attributes: SE_GROUP_ENABLED as u32,
            }],
            restricting_groups: vec![ObservedTokenGroup {
                sid: sid_from_text("S-1-5-21-111-222-333-1001").unwrap(),
                attributes: 0,
            }],
            capabilities: Vec::new(),
            privileges: vec![privilege(
                CHANGE_NOTIFY_PRIVILEGE,
                PRIVILEGE_CAPABILITY_MASK,
            )],
        }
    }

    fn authority_observation() -> ProcessTokenObservation {
        let service = sid_from_text(AUTHORITY_SERVICE_SID).unwrap();
        ProcessTokenObservation {
            token_type: TokenPrimary,
            session_id: 0,
            restricted: true,
            has_restrictions: 1,
            elevated: true,
            elevation_type: TokenElevationTypeDefault,
            user_sid: sid_from_text(LOCAL_SYSTEM_SID).unwrap(),
            user_attributes: 0,
            integrity_sid: sid_from_text(SYSTEM_INTEGRITY_SID).unwrap(),
            integrity_attributes: INTEGRITY_ATTRIBUTES,
            mandatory_policy: TOKEN_MANDATORY_POLICY_NO_WRITE_UP,
            ui_access: 0,
            virtualization_allowed: 0,
            virtualization_enabled: 0,
            sandbox_inert: 0,
            is_app_container: 0,
            app_container_sid: None,
            groups: vec![ObservedTokenGroup {
                sid: service.clone(),
                attributes: SERVICE_SID_ENABLED_ATTRIBUTES,
            }],
            restricting_groups: vec![ObservedTokenGroup {
                sid: service,
                attributes: 0,
            }],
            capabilities: Vec::new(),
            privileges: AUTHORITY_PRIVILEGES
                .iter()
                .map(|name| {
                    privilege(
                        name,
                        if *name == CHANGE_NOTIFY_PRIVILEGE {
                            PRIVILEGE_CAPABILITY_MASK
                        } else {
                            0
                        },
                    )
                })
                .collect(),
        }
    }

    #[test]
    fn canonical_measurement_accepts_only_the_two_exact_policy_shapes() {
        let runner = runner_observation()
            .validate_and_digest(ProcessTokenPolicy::DedicatedRestrictedRunner)
            .unwrap();
        let authority = authority_observation()
            .validate_and_digest(ProcessTokenPolicy::RestrictedAuthority)
            .unwrap();
        assert_ne!(runner, authority);
        assert_eq!(
            runner_observation()
                .validate_and_digest(ProcessTokenPolicy::DedicatedRestrictedRunner)
                .unwrap(),
            runner
        );
    }

    #[test]
    fn thread_token_probe_accepts_only_the_absence_of_impersonation() {
        let thread = unsafe { GetCurrentThread() };
        require_thread_without_impersonation_token(thread).unwrap();
        assert_ne!(unsafe { ImpersonateSelf(SecurityImpersonation) }, 0);
        let guard = RevertImpersonation;
        assert_eq!(
            require_thread_without_impersonation_token(thread)
                .unwrap_err()
                .code(),
            "protected_child_thread_impersonation_token_forbidden"
        );
        drop(guard);
        require_thread_without_impersonation_token(thread).unwrap();
    }

    #[test]
    fn embedded_sid_bounds_are_proved_before_any_sid_api_read() {
        let short = sensitive_buffer(&[0; 7]);
        let short_start = short.as_ptr().cast::<u8>();
        assert!(bounded_embedded_sid_length(&short, short_start).is_err());

        let mut truncated_body = [0u8; 11];
        truncated_body[1] = 1;
        let truncated_body = sensitive_buffer(&truncated_body);
        assert!(
            bounded_embedded_sid_length(&truncated_body, truncated_body.as_ptr().cast::<u8>())
                .is_err()
        );

        let mut maximum = [0u8; 68];
        maximum[0] = 1;
        maximum[1] = 15;
        let maximum = sensitive_buffer(&maximum);
        let start = maximum.as_ptr().cast::<u8>();
        assert_eq!(bounded_embedded_sid_length(&maximum, start).unwrap(), 68);
        assert!(bounded_embedded_sid_length(&maximum, start.wrapping_sub(1)).is_err());
        let end = unsafe { start.add(maximum.byte_len()) };
        assert!(bounded_embedded_sid_length(&maximum, end).is_err());
    }

    #[test]
    fn privilege_name_allocation_is_bounded_before_allocation() {
        assert!(bounded_privilege_name_capacity(0).is_err());
        assert_eq!(bounded_privilege_name_capacity(128).unwrap(), 129);
        assert!(bounded_privilege_name_capacity(129).is_err());
        assert!(bounded_privilege_name_capacity(u32::MAX).is_err());
    }

    #[test]
    fn type_integrity_mandatory_and_privilege_drift_are_rejected() {
        for index in 0..4 {
            let mut observed = runner_observation();
            match index {
                0 => observed.token_type += 1,
                1 => observed.integrity_sid = sid_from_text(SYSTEM_INTEGRITY_SID).unwrap(),
                2 => observed.mandatory_policy ^= 1,
                _ => observed.privileges.push(privilege("sedebugprivilege", 0)),
            }
            assert_eq!(
                observed
                    .validate_and_digest(ProcessTokenPolicy::DedicatedRestrictedRunner)
                    .unwrap_err()
                    .code(),
                "child_handshake_dedicated_runner_token_required"
            );
        }
    }

    #[test]
    fn runner_restriction_must_be_the_single_unattributed_user_sid() {
        for index in 0..4 {
            let mut observed = runner_observation();
            match index {
                0 => observed.restricting_groups[0].sid = sid_from_text("S-1-1-0").unwrap(),
                1 => observed.restricting_groups[0].sid = sid_from_text("S-1-5-32-545").unwrap(),
                2 => observed.restricting_groups.push(ObservedTokenGroup {
                    sid: observed.user_sid.clone(),
                    attributes: 0,
                }),
                _ => observed.restricting_groups[0].attributes = SE_GROUP_ENABLED as u32,
            }
            assert_eq!(
                observed
                    .validate_and_digest(ProcessTokenPolicy::DedicatedRestrictedRunner)
                    .unwrap_err()
                    .code(),
                "child_handshake_dedicated_runner_token_required"
            );
        }
    }

    #[test]
    fn parent_runner_measurement_requires_the_sealed_user_sid_without_digest_fork() {
        let observed = runner_observation();
        let expected = sid_from_text("S-1-5-21-111-222-333-1001").unwrap();
        let child_digest = observed
            .validate_and_digest(ProcessTokenPolicy::DedicatedRestrictedRunner)
            .unwrap();
        let parent_digest = observed
            .validate_and_digest_with_expected_runner(
                ProcessTokenPolicy::DedicatedRestrictedRunner,
                Some(&expected),
            )
            .unwrap();
        assert_eq!(parent_digest, child_digest);

        let wrong = sid_from_text("S-1-5-21-111-222-333-1002").unwrap();
        assert_eq!(
            observed
                .validate_and_digest_with_expected_runner(
                    ProcessTokenPolicy::DedicatedRestrictedRunner,
                    Some(&wrong),
                )
                .unwrap_err()
                .code(),
            "child_handshake_dedicated_runner_token_required"
        );
    }

    #[test]
    fn expected_runner_sid_requires_exact_canonical_round_trip_text() {
        ExpectedRestrictedRunnerSid::from_canonical_text("S-1-5-21-111-222-333-1001").unwrap();
        for value in [
            "s-1-5-21-111-222-333-1001",
            "S-1-5-21-0111-222-333-1001",
            "S-1-5-21-111-222-333-1001 ",
            "S-1-5-21-111-222-333-1001\0S-1-5-18",
        ] {
            assert_eq!(
                ExpectedRestrictedRunnerSid::from_canonical_text(value)
                    .unwrap_err()
                    .code(),
                "child_handshake_expected_runner_sid_invalid"
            );
        }
    }

    #[test]
    fn ui_app_container_group_and_authority_privilege_drift_are_rejected() {
        for index in 0..4 {
            let mut observed = authority_observation();
            match index {
                0 => observed.ui_access = 1,
                1 => observed.is_app_container = 1,
                2 => observed.groups[0].attributes ^= SE_GROUP_OWNER as u32,
                _ => observed.privileges[0].attributes = SE_PRIVILEGE_ENABLED as u32,
            }
            assert_eq!(
                observed
                    .validate_and_digest(ProcessTokenPolicy::RestrictedAuthority)
                    .unwrap_err()
                    .code(),
                "child_handshake_restricted_authority_token_required"
            );
        }
    }

    #[test]
    fn live_desktop_token_reaches_policy_validation_without_query_fallbacks() {
        let process = unsafe { windows_sys::Win32::System::Threading::GetCurrentProcess() };
        let mut raw_token: HANDLE = null_mut();
        assert_ne!(
            unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut raw_token) },
            0
        );
        let token = unsafe { OwnedHandle::from_raw_handle(raw_token as RawHandle) };
        let token = token.as_raw_handle().cast();
        for (name, class) in [
            ("user", TokenUser),
            ("integrity", TokenIntegrityLevel),
            ("elevation", TokenElevation),
            ("mandatory", TokenMandatoryPolicy),
            ("type", TokenType),
            ("session", TokenSessionId),
            ("has_restrictions", TokenHasRestrictions),
            ("elevation_type", TokenElevationType),
            ("ui_access", TokenUIAccess),
            ("virtualization_allowed", TokenVirtualizationAllowed),
            ("virtualization_enabled", TokenVirtualizationEnabled),
            ("sandbox", TokenSandBoxInert),
            ("app_container", TokenIsAppContainer),
            ("groups", TokenGroups),
            ("restricted_sids", TokenRestrictedSids),
            ("capabilities", TokenCapabilities),
            ("privileges", TokenPrivileges),
        ] {
            query_token_buffer(token, class)
                .unwrap_or_else(|error| panic!("{name}: {}", error.code()));
        }
        for (name, class) in [
            ("has_restrictions", TokenHasRestrictions),
            ("ui_access", TokenUIAccess),
            ("virtualization_allowed", TokenVirtualizationAllowed),
            ("virtualization_enabled", TokenVirtualizationEnabled),
            ("sandbox", TokenSandBoxInert),
            ("app_container", TokenIsAppContainer),
        ] {
            query_token_boolean(token, class)
                .unwrap_or_else(|error| panic!("{name}: {}", error.code()));
        }
        query_token_groups(token, TokenGroups, false)
            .unwrap_or_else(|error| panic!("groups: {}", error.code()));
        query_token_groups(token, TokenRestrictedSids, true)
            .unwrap_or_else(|error| panic!("restricted_sids: {}", error.code()));
        query_token_groups(token, TokenCapabilities, true)
            .unwrap_or_else(|error| panic!("capabilities: {}", error.code()));
        query_token_privileges(token)
            .unwrap_or_else(|error| panic!("privileges: {}", error.code()));
        assert_eq!(
            measure_process_token_digest(process, ProcessTokenPolicy::DedicatedRestrictedRunner)
                .unwrap_err()
                .code(),
            "child_handshake_dedicated_runner_token_required"
        );
    }
}
