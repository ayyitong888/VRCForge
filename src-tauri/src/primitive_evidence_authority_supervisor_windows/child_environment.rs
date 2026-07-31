//! Canonical, allowlisted environment block for protected child processes.
//!
//! The native launcher supplies already-verified directory capabilities and
//! this module projects only the fixed variables below. It never reads or
//! merges the service process environment, so credentials, proxy settings,
//! loader overrides, language runtimes, and debugging hooks cannot cross the
//! launch boundary by inheritance.

#![cfg_attr(not(test), allow(dead_code))]

use super::{Digest, SupervisorError};
use crate::{
    primitive_evidence_authority_install::bootstrap::AuthenticatedRunnerLaunchPolicy,
    primitive_evidence_child_protocol::windows_child_handshake::environment_observation_digest_from_utf16,
};
use sha2::{Digest as Sha2Digest, Sha256};
use std::{
    collections::BTreeMap,
    fmt,
    path::{Component, Path, PathBuf, Prefix},
    ptr,
    sync::atomic::{compiler_fence, Ordering},
};

const CHILD_ENVIRONMENT_BINDING_DOMAIN: &[u8] = b"vrcforge-native-child-environment-binding-v2\0";
const RUNNER_ENVIRONMENT_ROOTS_BINDING_DOMAIN: &[u8] =
    b"vrcforge-runner-environment-roots-binding-v1\0";
pub(super) const RUNNER_ENVIRONMENT_ROOTS_ACQUISITION_BLOCKER: &str =
    "authority_runner_environment_roots_machine_readback_not_connected";
pub(super) const RUNNER_ENVIRONMENT_ROOTS_LIVE_REVALIDATION_BLOCKER: &str =
    "authority_runner_environment_roots_live_same_object_readback_not_connected";
const MAX_ENVIRONMENT_UTF16_UNITS: usize = 32_767;
const FIXED_ENVIRONMENT_NAMES: [&str; 14] = [
    "APPDATA",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
];

/// Paths are values only. Their filesystem identity and ACL proof stay in the
/// native profile capability and must be revalidated before this projection is
/// passed to a process-creation call.
struct MinimalEnvironmentPaths {
    system_root: PathBuf,
    user_profile: PathBuf,
    local_app_data: PathBuf,
    roaming_app_data: PathBuf,
    program_data: PathBuf,
    temp_root: PathBuf,
}

impl MinimalEnvironmentPaths {
    fn new(
        system_root: PathBuf,
        user_profile: PathBuf,
        local_app_data: PathBuf,
        roaming_app_data: PathBuf,
        program_data: PathBuf,
        temp_root: PathBuf,
    ) -> Result<Self, SupervisorError> {
        for path in [
            &system_root,
            &user_profile,
            &local_app_data,
            &roaming_app_data,
            &program_data,
            &temp_root,
        ] {
            validate_absolute_environment_path(path)?;
        }
        if !path_is_strict_descendant(&user_profile, &local_app_data)
            || !path_is_strict_descendant(&user_profile, &roaming_app_data)
            || !path_is_strict_descendant(&user_profile, &temp_root)
            || path_is_same_or_descendant(&user_profile, &program_data)
            || path_is_same_or_descendant(&system_root, &user_profile)
            || path_is_same_or_descendant(&user_profile, &system_root)
        {
            return Err(SupervisorError::new(
                "authority_native_child_environment_paths_invalid",
            ));
        }
        Ok(Self {
            system_root,
            user_profile,
            local_app_data,
            roaming_app_data,
            program_data,
            temp_root,
        })
    }
}

/// Opaque generation-bound proof of the exact profile, system, shared-data,
/// and protected-install roots admitted by machine readback. The install root
/// is intentionally not projected into the child environment; it remains in
/// the binding so a path substitution cannot be ignored by the adapter.
///
/// Opening the product gate also requires either held same-object directory
/// handles spanning readback through process creation, or a fresh identity,
/// ACL, and reparse-point readback immediately before every create call. Path
/// text plus cached digests alone never satisfy that live authorization.
pub(super) struct VerifiedRunnerEnvironmentRootsCapability {
    generation: Digest,
    transaction_sha256: Digest,
    final_commit_receipt_sha256: Digest,
    runner_policy_state_binding_sha256: Digest,
    runner_profile_root: PathBuf,
    runner_profile_identity_sha256: Digest,
    runner_profile_security_sha256: Digest,
    system_root: PathBuf,
    system_root_identity_sha256: Digest,
    program_data: PathBuf,
    program_data_identity_sha256: Digest,
    install_root: PathBuf,
    install_root_identity_sha256: Digest,
    binding_digest: Digest,
}

impl fmt::Debug for VerifiedRunnerEnvironmentRootsCapability {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VerifiedRunnerEnvironmentRootsCapability(<held-and-redacted>)")
    }
}

impl VerifiedRunnerEnvironmentRootsCapability {
    /// Filesystem identity/ACL readback belongs to the privileged provisioning
    /// boundary and is deliberately not synthesized from service environment
    /// variables or caller-provided paths.
    pub(super) fn from_production_machine_readback() -> Result<Self, SupervisorError> {
        Err(SupervisorError::new(
            RUNNER_ENVIRONMENT_ROOTS_ACQUISITION_BLOCKER,
        ))
    }

    /// Deliberately closed until the capability owns the admitted directory
    /// objects or can freshly remeasure identity, ACL, and reparse state at the
    /// final process-creation boundary.
    pub(super) fn revalidate_live_same_objects_before_launch(&self) -> Result<(), SupervisorError> {
        Err(SupervisorError::new(
            RUNNER_ENVIRONMENT_ROOTS_LIVE_REVALIDATION_BLOCKER,
        ))
    }

    pub(super) fn verify_for(
        &self,
        authenticated: &AuthenticatedRunnerLaunchPolicy,
    ) -> Result<(), SupervisorError> {
        validate_absolute_environment_path(&self.install_root)?;
        let derived_paths = MinimalEnvironmentPaths::new(
            self.system_root.clone(),
            self.runner_profile_root.clone(),
            self.runner_profile_root.join(r"AppData\Local"),
            self.runner_profile_root.join(r"AppData\Roaming"),
            self.program_data.clone(),
            self.runner_profile_root.join(r"AppData\Local\Temp"),
        )?;
        drop(derived_paths);
        if self.generation != *authenticated.generation()
            || self.transaction_sha256 != *authenticated.transaction_sha256()
            || self.final_commit_receipt_sha256 != *authenticated.final_commit_receipt_sha256()
            || self.runner_policy_state_binding_sha256 != *authenticated.state_binding_sha256()
            || self.runner_profile_root != authenticated.profile_root()
            || self.runner_profile_identity_sha256 != *authenticated.profile_identity_sha256()
            || self.runner_profile_security_sha256 != *authenticated.profile_security_sha256()
            || [
                self.system_root_identity_sha256,
                self.program_data_identity_sha256,
                self.install_root_identity_sha256,
            ]
            .iter()
            .any(|digest| digest.iter().all(|byte| *byte == 0))
            || path_is_same_or_descendant(&self.runner_profile_root, &self.install_root)
            || path_is_same_or_descendant(&self.install_root, &self.runner_profile_root)
            || path_is_same_or_descendant(&self.system_root, &self.install_root)
            || path_is_same_or_descendant(&self.install_root, &self.system_root)
            || path_is_same_or_descendant(&self.program_data, &self.install_root)
            || path_is_same_or_descendant(&self.install_root, &self.program_data)
            || self.binding_digest != runner_environment_roots_binding(self)?
        {
            return Err(SupervisorError::new(
                "authority_runner_environment_roots_binding_mismatch",
            ));
        }
        Ok(())
    }

    pub(super) fn environment_for_run(
        &self,
        runner_profile_digest: Digest,
        authenticated: &AuthenticatedRunnerLaunchPolicy,
    ) -> Result<(CanonicalChildEnvironmentBlock, Digest), SupervisorError> {
        self.verify_for(authenticated)?;
        let binding_digest = self.binding_digest;
        let paths = MinimalEnvironmentPaths::new(
            self.system_root.clone(),
            self.runner_profile_root.clone(),
            self.runner_profile_root.join(r"AppData\Local"),
            self.runner_profile_root.join(r"AppData\Roaming"),
            self.program_data.clone(),
            self.runner_profile_root.join(r"AppData\Local\Temp"),
        )?;
        let environment =
            CanonicalChildEnvironmentBlock::from_verified_paths(runner_profile_digest, paths)?;
        Ok((environment, binding_digest))
    }

    #[cfg(test)]
    pub(super) fn exact_test_fixture(authenticated: &AuthenticatedRunnerLaunchPolicy) -> Self {
        let mut value = Self {
            generation: *authenticated.generation(),
            transaction_sha256: *authenticated.transaction_sha256(),
            final_commit_receipt_sha256: *authenticated.final_commit_receipt_sha256(),
            runner_policy_state_binding_sha256: *authenticated.state_binding_sha256(),
            runner_profile_root: authenticated.profile_root().to_path_buf(),
            runner_profile_identity_sha256: *authenticated.profile_identity_sha256(),
            runner_profile_security_sha256: *authenticated.profile_security_sha256(),
            system_root: PathBuf::from(r"C:\Windows"),
            system_root_identity_sha256: [0x81; 32],
            program_data: PathBuf::from(r"C:\ProgramData"),
            program_data_identity_sha256: [0x82; 32],
            install_root: PathBuf::from(r"C:\Program Files\VRCForgeEvidenceAuthority\v1"),
            install_root_identity_sha256: [0x83; 32],
            binding_digest: [0; 32],
        };
        value.binding_digest =
            runner_environment_roots_binding(&value).expect("root fixture binding");
        value
    }

    #[cfg(test)]
    pub(super) fn with_profile_root_drift_for_test(mut self) -> Self {
        self.runner_profile_root = PathBuf::from(r"C:\Users\OtherRunner");
        self
    }

    #[cfg(test)]
    pub(super) fn with_system_root_drift_for_test(mut self) -> Self {
        self.system_root = PathBuf::from(r"D:\Windows");
        self
    }

    #[cfg(test)]
    pub(super) fn with_install_root_drift_for_test(mut self) -> Self {
        self.install_root = PathBuf::from(r"D:\ProtectedRuntime\v1");
        self
    }
}

/// Exact double-NUL-terminated UTF-16 environment material. The binding
/// includes the policy's runner-profile digest and every encoded code unit.
pub(super) struct CanonicalChildEnvironmentBlock {
    utf16: Vec<u16>,
    working_directory_utf16: Vec<u16>,
    runner_profile_digest: Digest,
    binding_digest: Digest,
    observation_digest: Digest,
}

impl fmt::Debug for CanonicalChildEnvironmentBlock {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("CanonicalChildEnvironmentBlock(<redacted>)")
    }
}

impl Drop for CanonicalChildEnvironmentBlock {
    fn drop(&mut self) {
        zero_environment_material(
            &mut self.utf16,
            &mut self.working_directory_utf16,
            &mut self.runner_profile_digest,
            &mut self.binding_digest,
            &mut self.observation_digest,
        );
    }
}

impl CanonicalChildEnvironmentBlock {
    fn from_verified_paths(
        runner_profile_digest: Digest,
        paths: MinimalEnvironmentPaths,
    ) -> Result<Self, SupervisorError> {
        if runner_profile_digest.iter().all(|byte| *byte == 0) {
            return Err(SupervisorError::new(
                "authority_native_child_environment_profile_invalid",
            ));
        }

        let system_root = path_text(&paths.system_root)?;
        let user_profile = path_text(&paths.user_profile)?;
        let working_directory_utf16 = user_profile
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        if working_directory_utf16.len() <= 1
            || working_directory_utf16.len() > MAX_ENVIRONMENT_UTF16_UNITS
        {
            return Err(SupervisorError::new(
                "authority_native_child_environment_paths_invalid",
            ));
        }
        let local_app_data = path_text(&paths.local_app_data)?;
        let roaming_app_data = path_text(&paths.roaming_app_data)?;
        let program_data = path_text(&paths.program_data)?;
        let temp_root = path_text(&paths.temp_root)?;
        let system_drive = drive_text(&paths.system_root)?;
        let home_drive = drive_text(&paths.user_profile)?;
        let home_path = user_profile
            .strip_prefix(&home_drive)
            .filter(|value| value.starts_with('\\'))
            .ok_or_else(|| {
                SupervisorError::new("authority_native_child_environment_paths_invalid")
            })?;
        let system32 = format!("{system_root}\\System32");

        let mut entries = BTreeMap::new();
        insert_fixed(&mut entries, "APPDATA", roaming_app_data)?;
        insert_fixed(&mut entries, "COMSPEC", format!("{system32}\\cmd.exe"))?;
        insert_fixed(&mut entries, "HOMEDRIVE", home_drive)?;
        insert_fixed(&mut entries, "HOMEPATH", home_path.to_owned())?;
        insert_fixed(&mut entries, "LOCALAPPDATA", local_app_data)?;
        insert_fixed(
            &mut entries,
            "PATH",
            format!("{system32};{system_root};{system32}\\Wbem"),
        )?;
        insert_fixed(&mut entries, "PATHEXT", ".COM;.EXE;.BAT;.CMD".to_owned())?;
        insert_fixed(&mut entries, "PROGRAMDATA", program_data)?;
        insert_fixed(&mut entries, "SYSTEMDRIVE", system_drive)?;
        insert_fixed(&mut entries, "SYSTEMROOT", system_root.clone())?;
        insert_fixed(&mut entries, "TEMP", temp_root.clone())?;
        insert_fixed(&mut entries, "TMP", temp_root)?;
        insert_fixed(&mut entries, "USERPROFILE", user_profile)?;
        insert_fixed(&mut entries, "WINDIR", system_root)?;

        if entries.len() != FIXED_ENVIRONMENT_NAMES.len()
            || !FIXED_ENVIRONMENT_NAMES
                .iter()
                .all(|name| entries.contains_key(*name))
        {
            return Err(SupervisorError::new(
                "authority_native_child_environment_shape_invalid",
            ));
        }

        let mut utf16 = Vec::new();
        for (name, value) in &entries {
            utf16.extend(format!("{name}={value}").encode_utf16());
            utf16.push(0);
        }
        utf16.push(0);
        if utf16.len() <= 2 || utf16.len() > MAX_ENVIRONMENT_UTF16_UNITS {
            return Err(SupervisorError::new(
                "authority_native_child_environment_size_invalid",
            ));
        }
        let binding_digest =
            child_environment_binding(&runner_profile_digest, &utf16, &working_directory_utf16);
        let observation_digest = environment_observation_digest_from_utf16(&utf16)
            .map_err(|error| SupervisorError::new(error.code()))?;
        if observation_digest == binding_digest {
            return Err(SupervisorError::new(
                "authority_native_child_environment_binding_invalid",
            ));
        }
        Ok(Self {
            utf16,
            working_directory_utf16,
            runner_profile_digest,
            binding_digest,
            observation_digest,
        })
    }

    pub(super) fn as_utf16(&self) -> &[u16] {
        &self.utf16
    }

    /// Exact non-null working-directory input for `CreateProcessAsUserW`.
    /// It is the verified runner profile root and is separately NUL-terminated.
    pub(super) fn working_directory_utf16(&self) -> &[u16] {
        &self.working_directory_utf16
    }

    pub(super) fn binding_digest(&self) -> &Digest {
        &self.binding_digest
    }

    pub(super) fn runner_profile_digest(&self) -> &Digest {
        &self.runner_profile_digest
    }

    pub(super) fn observation_digest(&self) -> &Digest {
        &self.observation_digest
    }
}

fn insert_fixed(
    entries: &mut BTreeMap<&'static str, String>,
    name: &'static str,
    value: String,
) -> Result<(), SupervisorError> {
    if !FIXED_ENVIRONMENT_NAMES.contains(&name)
        || value.is_empty()
        || value
            .chars()
            .any(|value| value == '\0' || value.is_control())
        || entries.insert(name, value).is_some()
    {
        return Err(SupervisorError::new(
            "authority_native_child_environment_shape_invalid",
        ));
    }
    Ok(())
}

fn validate_absolute_environment_path(path: &Path) -> Result<(), SupervisorError> {
    let text = path.to_string_lossy();
    let bytes = text.as_bytes();
    let valid_drive = matches!(
        path.components().next(),
        Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::Disk(_))
    );
    if !path.is_absolute()
        || !valid_drive
        || text.is_empty()
        || bytes.len() < 3
        || !bytes[0].is_ascii_uppercase()
        || bytes[1] != b':'
        || bytes[2] != b'\\'
        || text.contains('/')
        || text.ends_with('\\')
        || text.ends_with('/')
        || text
            .chars()
            .any(|value| value == '\0' || value == '=' || value == ';' || value.is_control())
        || path
            .components()
            .any(|part| matches!(part, Component::CurDir | Component::ParentDir))
    {
        return Err(SupervisorError::new(
            "authority_native_child_environment_paths_invalid",
        ));
    }
    Ok(())
}

fn path_text(path: &Path) -> Result<String, SupervisorError> {
    validate_absolute_environment_path(path)?;
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| SupervisorError::new("authority_native_child_environment_paths_invalid"))
}

fn drive_text(path: &Path) -> Result<String, SupervisorError> {
    match path.components().next() {
        Some(Component::Prefix(prefix)) => match prefix.kind() {
            Prefix::Disk(letter) => Ok(format!("{}:", char::from(letter).to_ascii_uppercase())),
            _ => Err(SupervisorError::new(
                "authority_native_child_environment_paths_invalid",
            )),
        },
        _ => Err(SupervisorError::new(
            "authority_native_child_environment_paths_invalid",
        )),
    }
}

fn normalized_path(path: &Path) -> String {
    path.to_string_lossy()
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_ascii_lowercase()
}

fn path_is_same_or_descendant(parent: &Path, child: &Path) -> bool {
    let parent = normalized_path(parent);
    let child = normalized_path(child);
    child == parent
        || child
            .strip_prefix(&parent)
            .is_some_and(|suffix| suffix.starts_with('\\'))
}

fn path_is_strict_descendant(parent: &Path, child: &Path) -> bool {
    normalized_path(parent) != normalized_path(child) && path_is_same_or_descendant(parent, child)
}

fn runner_environment_roots_binding(
    value: &VerifiedRunnerEnvironmentRootsCapability,
) -> Result<Digest, SupervisorError> {
    let profile_root = path_text(&value.runner_profile_root)?;
    let system_root = path_text(&value.system_root)?;
    let program_data = path_text(&value.program_data)?;
    let install_root = path_text(&value.install_root)?;
    let mut hasher = Sha256::new();
    hasher.update(RUNNER_ENVIRONMENT_ROOTS_BINDING_DOMAIN);
    for digest in [
        &value.generation,
        &value.transaction_sha256,
        &value.final_commit_receipt_sha256,
        &value.runner_policy_state_binding_sha256,
        &value.runner_profile_identity_sha256,
        &value.runner_profile_security_sha256,
        &value.system_root_identity_sha256,
        &value.program_data_identity_sha256,
        &value.install_root_identity_sha256,
    ] {
        if digest.iter().all(|byte| *byte == 0) {
            return Err(SupervisorError::new(
                "authority_runner_environment_roots_binding_invalid",
            ));
        }
        hasher.update(digest);
    }
    for path in [profile_root, system_root, program_data, install_root] {
        hasher.update((path.len() as u64).to_be_bytes());
        hasher.update(path.as_bytes());
    }
    Ok(hasher.finalize().into())
}

fn child_environment_binding(
    runner_profile_digest: &Digest,
    utf16: &[u16],
    working_directory_utf16: &[u16],
) -> Digest {
    let mut hasher = Sha256::new();
    hasher.update(CHILD_ENVIRONMENT_BINDING_DOMAIN);
    hasher.update(runner_profile_digest);
    hasher.update((utf16.len() as u64).to_be_bytes());
    for value in utf16 {
        hasher.update(value.to_le_bytes());
    }
    hasher.update((working_directory_utf16.len() as u64).to_be_bytes());
    for value in working_directory_utf16 {
        hasher.update(value.to_le_bytes());
    }
    hasher.finalize().into()
}

fn zero_environment_material(
    utf16: &mut [u16],
    working_directory_utf16: &mut [u16],
    runner_profile_digest: &mut Digest,
    binding_digest: &mut Digest,
    observation_digest: &mut Digest,
) {
    for value in utf16 {
        unsafe { ptr::write_volatile(value, 0) };
    }
    for value in working_directory_utf16 {
        unsafe { ptr::write_volatile(value, 0) };
    }
    for digest in [runner_profile_digest, binding_digest, observation_digest] {
        for value in digest {
            unsafe { ptr::write_volatile(value, 0) };
        }
    }
    compiler_fence(Ordering::SeqCst);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;
    use std::os::windows::ffi::OsStringExt;

    fn paths() -> MinimalEnvironmentPaths {
        MinimalEnvironmentPaths::new(
            PathBuf::from(r"C:\Windows"),
            PathBuf::from(r"C:\Users\VRCForgeRunner"),
            PathBuf::from(r"C:\Users\VRCForgeRunner\AppData\Local"),
            PathBuf::from(r"C:\Users\VRCForgeRunner\AppData\Roaming"),
            PathBuf::from(r"C:\ProgramData"),
            PathBuf::from(r"C:\Users\VRCForgeRunner\AppData\Local\Temp"),
        )
        .unwrap()
    }

    fn decode(block: &[u16]) -> Vec<String> {
        assert_eq!(block.last(), Some(&0));
        assert_eq!(block.get(block.len() - 2), Some(&0));
        block[..block.len() - 1]
            .split(|value| *value == 0)
            .filter(|value| !value.is_empty())
            .map(|value| String::from_utf16(value).unwrap())
            .collect()
    }

    #[test]
    fn production_root_acquisition_and_live_object_revalidation_remain_closed() {
        assert_eq!(
            VerifiedRunnerEnvironmentRootsCapability::from_production_machine_readback()
                .unwrap_err()
                .code(),
            RUNNER_ENVIRONMENT_ROOTS_ACQUISITION_BLOCKER
        );
        let authenticated =
            AuthenticatedRunnerLaunchPolicy::exact_test_fixture([0x71; 32], [0x72; 32]);
        let roots = VerifiedRunnerEnvironmentRootsCapability::exact_test_fixture(&authenticated);
        assert_eq!(
            roots
                .revalidate_live_same_objects_before_launch()
                .unwrap_err()
                .code(),
            RUNNER_ENVIRONMENT_ROOTS_LIVE_REVALIDATION_BLOCKER
        );
        let production = include_str!("child_environment.rs")
            .split("#[cfg(test)]\nmod tests")
            .next()
            .expect("environment production source");
        assert!(production.contains("held same-object directory"));
        assert!(production.contains("fresh identity,"));
        assert!(production.contains("ACL, and reparse-point readback"));
    }

    #[test]
    fn environment_is_exact_sorted_double_terminated_and_policy_bound() {
        std::env::set_var("VRCFORGE_TEST_SHOULD_NOT_BE_INHERITED", "secret");
        let first = CanonicalChildEnvironmentBlock::from_verified_paths([0x41; 32], paths())
            .expect("fixed environment");
        let entries = decode(first.as_utf16());
        let names = entries
            .iter()
            .map(|entry| entry.split_once('=').unwrap().0)
            .collect::<Vec<_>>();
        assert_eq!(names, FIXED_ENVIRONMENT_NAMES);
        assert_eq!(entries.len(), FIXED_ENVIRONMENT_NAMES.len());
        assert!(entries
            .iter()
            .all(|entry| !entry.contains("VRCFORGE_TEST_SHOULD_NOT_BE_INHERITED")));
        assert!(entries
            .iter()
            .any(|entry| entry == r"PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem"));

        let same = CanonicalChildEnvironmentBlock::from_verified_paths([0x41; 32], paths())
            .expect("same fixed environment");
        let other = CanonicalChildEnvironmentBlock::from_verified_paths([0x42; 32], paths())
            .expect("other policy binding");
        assert_eq!(first.as_utf16(), same.as_utf16());
        assert_eq!(first.binding_digest(), same.binding_digest());
        assert_ne!(first.binding_digest(), other.binding_digest());
        assert_eq!(first.runner_profile_digest(), &[0x41; 32]);
        assert_eq!(other.runner_profile_digest(), &[0x42; 32]);
        assert_eq!(first.observation_digest(), same.observation_digest(),);
        assert_eq!(first.observation_digest(), other.observation_digest(),);
        assert_ne!(first.binding_digest(), first.observation_digest());
        assert_eq!(
            String::from_utf16(
                &first.working_directory_utf16()[..first.working_directory_utf16().len() - 1]
            )
            .unwrap(),
            r"C:\Users\VRCForgeRunner"
        );
        assert_eq!(first.working_directory_utf16().last(), Some(&0));
        assert_eq!(
            format!("{first:?}"),
            "CanonicalChildEnvironmentBlock(<redacted>)"
        );
        std::env::remove_var("VRCFORGE_TEST_SHOULD_NOT_BE_INHERITED");
    }

    #[test]
    fn environment_material_uses_the_volatile_zeroization_path() {
        let mut utf16 = vec![0x41u16, 0x42, 0];
        let mut working_directory_utf16 = vec![0x43u16, 0x44, 0];
        let mut profile = [0x41; 32];
        let mut binding = [0x51; 32];
        let mut observation = [0x61; 32];
        zero_environment_material(
            &mut utf16,
            &mut working_directory_utf16,
            &mut profile,
            &mut binding,
            &mut observation,
        );
        assert!(utf16.iter().all(|value| *value == 0));
        assert!(working_directory_utf16.iter().all(|value| *value == 0));
        assert_eq!(profile, [0; 32]);
        assert_eq!(binding, [0; 32]);
        assert_eq!(observation, [0; 32]);
    }

    #[test]
    fn environment_rejects_untrusted_path_shapes_and_zero_profile_binding() {
        let invalid = [
            PathBuf::from(r"relative\profile"),
            PathBuf::from(r"C:\Users\Runner\..\Other"),
            PathBuf::from("C:\\Users\\Bad;Path"),
            PathBuf::from("C:\\Users\\Bad=Path"),
            PathBuf::from("C:\\Users\\Bad\0Path"),
            PathBuf::from(r"c:\Users\Runner"),
            PathBuf::from("C:/Users/Runner"),
            PathBuf::from(r"\\server\share\Runner"),
        ];
        for value in invalid {
            assert!(MinimalEnvironmentPaths::new(
                PathBuf::from(r"C:\Windows"),
                value,
                PathBuf::from(r"C:\Users\Runner\AppData\Local"),
                PathBuf::from(r"C:\Users\Runner\AppData\Roaming"),
                PathBuf::from(r"C:\ProgramData"),
                PathBuf::from(r"C:\Users\Runner\AppData\Local\Temp"),
            )
            .is_err());
        }
        assert_eq!(
            CanonicalChildEnvironmentBlock::from_verified_paths([0; 32], paths())
                .unwrap_err()
                .code(),
            "authority_native_child_environment_profile_invalid"
        );
    }

    #[test]
    fn environment_requires_private_profile_descendants_and_separate_system_roots() {
        for (local, roaming, program_data, temp) in [
            (
                r"D:\Other\Local",
                r"C:\Users\VRCForgeRunner\AppData\Roaming",
                r"C:\ProgramData",
                r"C:\Users\VRCForgeRunner\Temp",
            ),
            (
                r"C:\Users\VRCForgeRunner\AppData\Local",
                r"D:\Other\Roaming",
                r"C:\ProgramData",
                r"C:\Users\VRCForgeRunner\Temp",
            ),
            (
                r"C:\Users\VRCForgeRunner\AppData\Local",
                r"C:\Users\VRCForgeRunner\AppData\Roaming",
                r"C:\Users\VRCForgeRunner\ProgramData",
                r"C:\Users\VRCForgeRunner\Temp",
            ),
            (
                r"C:\Users\VRCForgeRunner\AppData\Local",
                r"C:\Users\VRCForgeRunner\AppData\Roaming",
                r"C:\ProgramData",
                r"D:\Other\Temp",
            ),
        ] {
            assert!(MinimalEnvironmentPaths::new(
                PathBuf::from(r"C:\Windows"),
                PathBuf::from(r"C:\Users\VRCForgeRunner"),
                PathBuf::from(local),
                PathBuf::from(roaming),
                PathBuf::from(program_data),
                PathBuf::from(temp),
            )
            .is_err());
        }
    }

    #[test]
    fn fixed_projection_has_no_dynamic_variable_name_surface() {
        let block = CanonicalChildEnvironmentBlock::from_verified_paths([0x51; 32], paths())
            .expect("fixed environment");
        let names = decode(block.as_utf16())
            .into_iter()
            .map(|entry| entry.split_once('=').unwrap().0.to_owned())
            .collect::<Vec<_>>();
        assert_eq!(names, FIXED_ENVIRONMENT_NAMES.map(str::to_owned));
        let forbidden = [
            "RUNTIME_PATH_OVERRIDE",
            "RUNTIME_HOME_OVERRIDE",
            "PROCESS_OPTIONS",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "CLOUD_ACCESS_KEY_ID",
            "SERVICE_API_KEY",
            "SSLKEYLOGFILE",
            "COMPAT_LAYER_OVERRIDE",
        ];
        assert!(forbidden
            .iter()
            .all(|name| !names.iter().any(|candidate| candidate == name)));
        let _: OsString = OsString::from_wide(&block.as_utf16()[..block.as_utf16().len() - 2]);
    }
}
