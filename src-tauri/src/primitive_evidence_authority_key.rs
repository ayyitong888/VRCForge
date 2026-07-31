use sha2::{Digest, Sha256};
use std::fmt;

struct PendingCreatedResource<T> {
    value: Option<T>,
    cleanup: fn(&mut T),
}

impl<T> PendingCreatedResource<T> {
    fn new(value: T, cleanup: fn(&mut T)) -> Self {
        Self {
            value: Some(value),
            cleanup,
        }
    }

    fn value(&self) -> &T {
        self.value.as_ref().expect("pending resource is armed")
    }

    fn commit(mut self) -> T {
        self.value.take().expect("pending resource is armed")
    }
}

impl<T> Drop for PendingCreatedResource<T> {
    fn drop(&mut self) {
        if let Some(value) = self.value.as_mut() {
            (self.cleanup)(value);
        }
    }
}

pub const AUTHORITY_KEY_NAME_PREFIX: &str = "VRCForge.PrimitiveEvidence.Authority.P256.v1.";

const DIGEST_SIZE: usize = 32;
const PUBLIC_KEY_SIZE: usize = 65;
const P256_PUBLIC_BLOB_MAGIC: u32 = 0x3153_4345;
const P256_COORDINATE_SIZE: u32 = 32;
const P256_KEY_LENGTH_BITS: u32 = 256;
const MACHINE_KEY_FLAG: u32 = 0x0000_0020;
const SILENT_FLAG: u32 = 0x0000_0040;
const OPEN_EXISTING_FLAGS: u32 = MACHINE_KEY_FLAG | SILENT_FLAG;
const SIGN_ONLY_USAGE: u32 = 0x0000_0002;
const NO_EXPORT_POLICY: u32 = 0;
const EXPECTED_ALGORITHM: &str = "ECDSA_P256";
const EXPECTED_ALGORITHM_GROUP: &str = "ECDSA";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityKeyError(&'static str);

impl AuthorityKeyError {
    pub fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for AuthorityKeyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for AuthorityKeyError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityKeyPolicy {
    key_name: String,
    expected_signer_key_id: [u8; DIGEST_SIZE],
    expected_security_descriptor_sddl: String,
}

impl AuthorityKeyPolicy {
    pub fn new(
        authority_generation_digest: [u8; DIGEST_SIZE],
        expected_signer_key_id: [u8; DIGEST_SIZE],
        service_sid: &str,
    ) -> Result<Self, AuthorityKeyError> {
        if expected_signer_key_id.iter().all(|value| *value == 0) {
            return Err(AuthorityKeyError("authority_signer_key_id_zero"));
        }
        validate_service_sid(service_sid)?;
        Ok(Self {
            key_name: derive_machine_key_name(&authority_generation_digest)?,
            expected_signer_key_id,
            expected_security_descriptor_sddl: format!(
                "O:SYG:SYD:P(A;;GA;;;SY)(A;;GA;;;{service_sid})"
            ),
        })
    }

    pub fn key_name(&self) -> &str {
        &self.key_name
    }

    pub fn expected_signer_key_id(&self) -> &[u8; DIGEST_SIZE] {
        &self.expected_signer_key_id
    }

    pub fn expected_security_descriptor_sddl(&self) -> &str {
        &self.expected_security_descriptor_sddl
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedAuthorityKeyReadback {
    key_name: String,
    signer_key_id: [u8; DIGEST_SIZE],
    public_key_sec1: [u8; PUBLIC_KEY_SIZE],
}

impl VerifiedAuthorityKeyReadback {
    pub fn key_name(&self) -> &str {
        &self.key_name
    }

    pub fn signer_key_id(&self) -> &[u8; DIGEST_SIZE] {
        &self.signer_key_id
    }

    pub fn signer_key_id_hex(&self) -> String {
        hex_lower(&self.signer_key_id)
    }

    pub fn public_key_sec1(&self) -> &[u8; PUBLIC_KEY_SIZE] {
        &self.public_key_sec1
    }

    #[cfg(test)]
    pub(crate) fn for_test(public_key_sec1: [u8; PUBLIC_KEY_SIZE]) -> Self {
        assert_eq!(public_key_sec1[0], 0x04);
        assert!(public_key_sec1[1..].iter().any(|value| *value != 0));
        Self {
            key_name: "test-only-authority-key".to_string(),
            signer_key_id: Sha256::digest(public_key_sec1).into(),
            public_key_sec1,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthorityKeyReadback {
    Absent { key_name: String },
    Verified(VerifiedAuthorityKeyReadback),
}

impl AuthorityKeyReadback {
    pub fn is_absent(&self) -> bool {
        matches!(self, Self::Absent { .. })
    }

    pub fn key_name(&self) -> &str {
        match self {
            Self::Absent { key_name } => key_name,
            Self::Verified(readback) => readback.key_name(),
        }
    }
}

pub fn derive_machine_key_name(
    authority_generation_digest: &[u8; DIGEST_SIZE],
) -> Result<String, AuthorityKeyError> {
    if authority_generation_digest.iter().all(|value| *value == 0) {
        return Err(AuthorityKeyError("authority_generation_digest_zero"));
    }
    Ok(format!(
        "{AUTHORITY_KEY_NAME_PREFIX}{}",
        hex_lower(authority_generation_digest)
    ))
}

#[cfg(windows)]
pub fn inspect_existing_machine_key(
    policy: &AuthorityKeyPolicy,
) -> Result<AuthorityKeyReadback, AuthorityKeyError> {
    windows::inspect_existing_machine_key(policy)
}

#[cfg(windows)]
pub fn provision_new_machine_key(
    authority_generation_digest: [u8; DIGEST_SIZE],
    service_sid: &str,
) -> Result<ProvisionedAuthorityKey, AuthorityKeyError> {
    windows::provision_new_machine_key(authority_generation_digest, service_sid)
}

#[cfg(windows)]
pub fn open_verified_machine_key(
    policy: &AuthorityKeyPolicy,
) -> Result<OpenedAuthorityKey, AuthorityKeyError> {
    windows::open_verified_machine_key(policy)
}

#[cfg(windows)]
pub use windows::{OpenedAuthorityKey, ProvisionedAuthorityKey};

#[cfg(not(windows))]
pub fn inspect_existing_machine_key(
    _policy: &AuthorityKeyPolicy,
) -> Result<AuthorityKeyReadback, AuthorityKeyError> {
    Err(AuthorityKeyError("authority_key_platform_unsupported"))
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum AuthorityKeySnapshot {
    Absent,
    Present(Box<AuthorityKeyPropertySnapshot>),
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct AuthorityKeyPropertySnapshot {
    open_flags: u32,
    name: Option<String>,
    key_type: Option<u32>,
    algorithm: Option<String>,
    algorithm_group: Option<String>,
    key_length_bits: Option<u32>,
    key_usage: Option<u32>,
    export_policy: Option<u32>,
    public_blob_magic: Option<u32>,
    public_coordinate_size: Option<u32>,
    public_key_sec1: Option<Vec<u8>>,
    security_descriptor_sddl: Option<String>,
    unrecognized_properties: Vec<String>,
}

fn validate_snapshot(
    policy: &AuthorityKeyPolicy,
    snapshot: AuthorityKeySnapshot,
) -> Result<AuthorityKeyReadback, AuthorityKeyError> {
    let properties = match snapshot {
        AuthorityKeySnapshot::Absent => {
            return Ok(AuthorityKeyReadback::Absent {
                key_name: policy.key_name.clone(),
            });
        }
        AuthorityKeySnapshot::Present(properties) => properties,
    };

    if !properties.unrecognized_properties.is_empty() {
        return Err(AuthorityKeyError(
            "authority_key_unrecognized_property_present",
        ));
    }
    if properties.open_flags != OPEN_EXISTING_FLAGS {
        return Err(AuthorityKeyError("authority_key_open_flags_mismatch"));
    }
    if require(properties.name.as_deref(), "authority_key_name_missing")? != policy.key_name {
        return Err(AuthorityKeyError("authority_key_name_mismatch"));
    }
    if require(properties.key_type, "authority_key_type_missing")? != MACHINE_KEY_FLAG {
        return Err(AuthorityKeyError("authority_key_not_machine_scoped"));
    }
    if require(
        properties.algorithm.as_deref(),
        "authority_key_algorithm_missing",
    )? != EXPECTED_ALGORITHM
    {
        return Err(AuthorityKeyError("authority_key_algorithm_mismatch"));
    }
    if require(
        properties.algorithm_group.as_deref(),
        "authority_key_algorithm_group_missing",
    )? != EXPECTED_ALGORITHM_GROUP
    {
        return Err(AuthorityKeyError("authority_key_algorithm_group_mismatch"));
    }
    if require(properties.key_length_bits, "authority_key_length_missing")? != P256_KEY_LENGTH_BITS
    {
        return Err(AuthorityKeyError("authority_key_length_mismatch"));
    }
    if require(properties.key_usage, "authority_key_usage_missing")? != SIGN_ONLY_USAGE {
        return Err(AuthorityKeyError("authority_key_usage_not_sign_only"));
    }
    if require(
        properties.export_policy,
        "authority_key_export_policy_missing",
    )? != NO_EXPORT_POLICY
    {
        return Err(AuthorityKeyError("authority_key_exportable"));
    }
    if require(
        properties.public_blob_magic,
        "authority_key_public_blob_magic_missing",
    )? != P256_PUBLIC_BLOB_MAGIC
    {
        return Err(AuthorityKeyError(
            "authority_key_public_blob_magic_mismatch",
        ));
    }
    if require(
        properties.public_coordinate_size,
        "authority_key_public_coordinate_size_missing",
    )? != P256_COORDINATE_SIZE
    {
        return Err(AuthorityKeyError(
            "authority_key_public_coordinate_size_mismatch",
        ));
    }

    let public_key = require(
        properties.public_key_sec1.as_deref(),
        "authority_key_public_key_missing",
    )?;
    if public_key.len() != PUBLIC_KEY_SIZE
        || public_key[0] != 0x04
        || public_key[1..].iter().all(|value| *value == 0)
    {
        return Err(AuthorityKeyError("authority_key_public_key_invalid"));
    }
    let public_key: [u8; PUBLIC_KEY_SIZE] = public_key
        .try_into()
        .map_err(|_| AuthorityKeyError("authority_key_public_key_invalid"))?;
    let signer_key_id: [u8; DIGEST_SIZE] = Sha256::digest(public_key).into();
    if signer_key_id != policy.expected_signer_key_id {
        return Err(AuthorityKeyError("authority_signer_key_id_mismatch"));
    }

    if require(
        properties.security_descriptor_sddl.as_deref(),
        "authority_key_security_descriptor_missing",
    )? != policy.expected_security_descriptor_sddl
    {
        return Err(AuthorityKeyError(
            "authority_key_security_descriptor_mismatch",
        ));
    }

    Ok(AuthorityKeyReadback::Verified(
        VerifiedAuthorityKeyReadback {
            key_name: policy.key_name.clone(),
            signer_key_id,
            public_key_sec1: public_key,
        },
    ))
}

fn require<T>(value: Option<T>, code: &'static str) -> Result<T, AuthorityKeyError> {
    value.ok_or(AuthorityKeyError(code))
}

fn validate_service_sid(value: &str) -> Result<(), AuthorityKeyError> {
    let parts = value.split('-').collect::<Vec<_>>();
    if parts.len() != 9 || parts[..4] != ["S", "1", "5", "80"] {
        return Err(AuthorityKeyError("authority_service_sid_invalid"));
    }
    for part in &parts[4..] {
        if part.is_empty()
            || (part.len() > 1 && part.starts_with('0'))
            || part.parse::<u32>().is_err()
        {
            return Err(AuthorityKeyError("authority_service_sid_invalid"));
        }
    }
    if parts[4..].iter().all(|part| *part == "0") {
        return Err(AuthorityKeyError("authority_service_sid_invalid"));
    }
    Ok(())
}

fn hex_lower(value: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::ptr::{null, null_mut};
    use windows_sys::{
        core::{PCWSTR, PWSTR},
        Win32::{
            Foundation::{LocalFree, NTE_BAD_KEYSET, NTE_EXISTS},
            Security::{
                Authorization::{
                    ConvertSecurityDescriptorToStringSecurityDescriptorW, SDDL_REVISION_1,
                },
                Cryptography::{
                    NCryptCreatePersistedKey, NCryptDeleteKey, NCryptExportKey, NCryptFinalizeKey,
                    NCryptFreeObject, NCryptGetProperty, NCryptOpenKey, NCryptOpenStorageProvider,
                    NCryptSetProperty, NCryptSignHash, NCryptVerifySignature,
                    BCRYPT_ECCPUBLIC_BLOB, BCRYPT_ECDSA_P256_ALGORITHM,
                    BCRYPT_ECDSA_PUBLIC_P256_MAGIC, MS_KEY_STORAGE_PROVIDER,
                    NCRYPT_ALGORITHM_GROUP_PROPERTY, NCRYPT_ALGORITHM_PROPERTY,
                    NCRYPT_EXPORT_POLICY_PROPERTY, NCRYPT_KEY_HANDLE, NCRYPT_KEY_TYPE_PROPERTY,
                    NCRYPT_KEY_USAGE_PROPERTY, NCRYPT_LENGTH_PROPERTY, NCRYPT_MACHINE_KEY_FLAG,
                    NCRYPT_NAME_PROPERTY, NCRYPT_PERSIST_FLAG, NCRYPT_PROV_HANDLE,
                    NCRYPT_SECURITY_DESCR_PROPERTY, NCRYPT_SILENT_FLAG,
                },
                DACL_SECURITY_INFORMATION, GROUP_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION,
                PSECURITY_DESCRIPTOR,
            },
        },
    };

    const MAX_STRING_PROPERTY_BYTES: u32 = 2_048;
    const MAX_SECURITY_DESCRIPTOR_BYTES: u32 = 16_384;
    const SECURITY_INFORMATION: u32 =
        OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION;
    const P256_SIGNATURE_SIZE: usize = 64;
    pub(super) const P256_ORDER: [u8; 32] = [
        0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xbc, 0xe6, 0xfa, 0xad, 0xa7, 0x17, 0x9e, 0x84, 0xf3, 0xb9, 0xca, 0xc2, 0xfc, 0x63,
        0x25, 0x51,
    ];
    pub(super) const P256_HALF_ORDER: [u8; 32] = [
        0x7f, 0xff, 0xff, 0xff, 0x80, 0x00, 0x00, 0x00, 0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xde, 0x73, 0x7d, 0x56, 0xd3, 0x8b, 0xcf, 0x42, 0x79, 0xdc, 0xe5, 0x61, 0x7e, 0x31,
        0x92, 0xa8,
    ];

    struct Provider(NCRYPT_PROV_HANDLE);

    impl Provider {
        fn open() -> Result<Self, AuthorityKeyError> {
            let mut handle = 0;
            let status =
                unsafe { NCryptOpenStorageProvider(&mut handle, MS_KEY_STORAGE_PROVIDER, 0) };
            check(status, "authority_key_provider_unavailable")?;
            if handle == 0 {
                return Err(AuthorityKeyError("authority_key_provider_unavailable"));
            }
            Ok(Self(handle))
        }

        fn open_existing_machine_key(
            &self,
            key_name: &str,
        ) -> Result<Option<Key>, AuthorityKeyError> {
            let wide_name = wide_null(key_name)?;
            let mut handle = 0;
            let flags = NCRYPT_MACHINE_KEY_FLAG | NCRYPT_SILENT_FLAG;
            if flags != OPEN_EXISTING_FLAGS {
                return Err(AuthorityKeyError("authority_key_open_flags_mismatch"));
            }
            let status =
                unsafe { NCryptOpenKey(self.0, &mut handle, wide_name.as_ptr(), 0, flags) };
            if status == NTE_BAD_KEYSET {
                return Ok(None);
            }
            check(status, "authority_key_open_existing_failed")?;
            if handle == 0 {
                return Err(AuthorityKeyError("authority_key_open_existing_failed"));
            }
            Ok(Some(Key(handle)))
        }

        fn create_new_machine_key(
            &self,
            key_name: &str,
            security_descriptor_sddl: &str,
        ) -> Result<PendingCreatedResource<Key>, AuthorityKeyError> {
            let wide_name = wide_null(key_name)?;
            let mut handle = 0;
            let flags = NCRYPT_MACHINE_KEY_FLAG | NCRYPT_SILENT_FLAG;
            let status = unsafe {
                NCryptCreatePersistedKey(
                    self.0,
                    &mut handle,
                    BCRYPT_ECDSA_P256_ALGORITHM,
                    wide_name.as_ptr(),
                    0,
                    flags,
                )
            };
            if status == NTE_EXISTS {
                return Err(AuthorityKeyError("authority_key_already_exists"));
            }
            check(status, "authority_key_create_failed")?;
            if handle == 0 {
                return Err(AuthorityKeyError("authority_key_create_failed"));
            }
            let key = PendingCreatedResource::new(Key(handle), delete_pending_key);
            key.value()
                .set_u32_property(NCRYPT_LENGTH_PROPERTY, P256_KEY_LENGTH_BITS)?;
            key.value()
                .set_u32_property(NCRYPT_KEY_USAGE_PROPERTY, SIGN_ONLY_USAGE)?;
            key.value()
                .set_u32_property(NCRYPT_EXPORT_POLICY_PROPERTY, NO_EXPORT_POLICY)?;
            key.value()
                .set_security_descriptor(security_descriptor_sddl)?;
            check(
                unsafe { NCryptFinalizeKey(key.value().0, NCRYPT_SILENT_FLAG) },
                "authority_key_finalize_failed",
            )?;
            Ok(key)
        }
    }

    impl Drop for Provider {
        fn drop(&mut self) {
            if self.0 != 0 {
                unsafe {
                    NCryptFreeObject(self.0);
                }
                self.0 = 0;
            }
        }
    }

    struct Key(NCRYPT_KEY_HANDLE);

    fn delete_pending_key(key: &mut Key) {
        let handle = key.0;
        key.0 = 0;
        if handle != 0 {
            unsafe {
                NCryptDeleteKey(handle, 0);
            }
        }
    }

    impl Key {
        fn snapshot(&self) -> Result<AuthorityKeyPropertySnapshot, AuthorityKeyError> {
            let (public_blob_magic, public_coordinate_size, public_key_sec1) = self.public_key()?;
            Ok(AuthorityKeyPropertySnapshot {
                open_flags: OPEN_EXISTING_FLAGS,
                name: Some(self.string_property(NCRYPT_NAME_PROPERTY)?),
                key_type: Some(self.u32_property(NCRYPT_KEY_TYPE_PROPERTY)?),
                algorithm: Some(self.string_property(NCRYPT_ALGORITHM_PROPERTY)?),
                algorithm_group: Some(self.string_property(NCRYPT_ALGORITHM_GROUP_PROPERTY)?),
                key_length_bits: Some(self.u32_property(NCRYPT_LENGTH_PROPERTY)?),
                key_usage: Some(self.u32_property(NCRYPT_KEY_USAGE_PROPERTY)?),
                export_policy: Some(self.u32_property(NCRYPT_EXPORT_POLICY_PROPERTY)?),
                public_blob_magic: Some(public_blob_magic),
                public_coordinate_size: Some(public_coordinate_size),
                public_key_sec1: Some(public_key_sec1.to_vec()),
                security_descriptor_sddl: Some(self.security_descriptor_sddl()?),
                unrecognized_properties: Vec::new(),
            })
        }

        fn u32_property(&self, property: PCWSTR) -> Result<u32, AuthorityKeyError> {
            let bytes = self.property_bytes(property, 4, NCRYPT_SILENT_FLAG)?;
            if bytes.len() != 4 {
                return Err(AuthorityKeyError("authority_key_property_shape_invalid"));
            }
            Ok(u32::from_le_bytes(bytes.as_slice().try_into().map_err(
                |_| AuthorityKeyError("authority_key_property_shape_invalid"),
            )?))
        }

        fn set_u32_property(&self, property: PCWSTR, value: u32) -> Result<(), AuthorityKeyError> {
            check(
                unsafe {
                    NCryptSetProperty(
                        self.0,
                        property,
                        value.to_le_bytes().as_ptr(),
                        4,
                        NCRYPT_PERSIST_FLAG | NCRYPT_SILENT_FLAG,
                    )
                },
                "authority_key_property_write_failed",
            )
        }

        fn set_security_descriptor(&self, sddl: &str) -> Result<(), AuthorityKeyError> {
            let encoded = wide_null(sddl)?;
            let mut descriptor = null_mut();
            let mut descriptor_length = 0u32;
            let converted = unsafe {
                windows_sys::Win32::Security::Authorization::
                    ConvertStringSecurityDescriptorToSecurityDescriptorW(
                        encoded.as_ptr(),
                        SDDL_REVISION_1,
                        &mut descriptor,
                        &mut descriptor_length,
                    )
            };
            if converted == 0 || descriptor.is_null() || descriptor_length == 0 {
                if !descriptor.is_null() {
                    unsafe {
                        LocalFree(descriptor);
                    }
                }
                return Err(AuthorityKeyError(
                    "authority_key_security_descriptor_invalid",
                ));
            }
            let status = unsafe {
                NCryptSetProperty(
                    self.0,
                    NCRYPT_SECURITY_DESCR_PROPERTY,
                    descriptor.cast::<u8>(),
                    descriptor_length,
                    SECURITY_INFORMATION | NCRYPT_PERSIST_FLAG | NCRYPT_SILENT_FLAG,
                )
            };
            unsafe {
                LocalFree(descriptor);
            }
            check(status, "authority_key_security_descriptor_write_failed")
        }

        fn sign_digest(&self, digest: &[u8; 32]) -> Result<[u8; 64], AuthorityKeyError> {
            if digest.iter().all(|value| *value == 0) {
                return Err(AuthorityKeyError("authority_signature_digest_zero"));
            }
            let mut required = 0u32;
            check(
                unsafe {
                    NCryptSignHash(
                        self.0,
                        null(),
                        digest.as_ptr(),
                        digest.len() as u32,
                        null_mut(),
                        0,
                        &mut required,
                        NCRYPT_SILENT_FLAG,
                    )
                },
                "authority_key_sign_failed",
            )?;
            if required != P256_SIGNATURE_SIZE as u32 {
                return Err(AuthorityKeyError("authority_signature_shape_invalid"));
            }
            let mut signature = [0u8; P256_SIGNATURE_SIZE];
            let mut written = 0u32;
            check(
                unsafe {
                    NCryptSignHash(
                        self.0,
                        null(),
                        digest.as_ptr(),
                        digest.len() as u32,
                        signature.as_mut_ptr(),
                        signature.len() as u32,
                        &mut written,
                        NCRYPT_SILENT_FLAG,
                    )
                },
                "authority_key_sign_failed",
            )?;
            if written != signature.len() as u32 {
                return Err(AuthorityKeyError("authority_signature_shape_invalid"));
            }
            normalize_low_s(&mut signature);
            check(
                unsafe {
                    NCryptVerifySignature(
                        self.0,
                        null(),
                        digest.as_ptr(),
                        digest.len() as u32,
                        signature.as_ptr(),
                        signature.len() as u32,
                        NCRYPT_SILENT_FLAG,
                    )
                },
                "authority_key_signature_self_verify_failed",
            )?;
            Ok(signature)
        }

        fn verify_digest_signature(
            &self,
            digest: &[u8; 32],
            signature: &[u8; P256_SIGNATURE_SIZE],
        ) -> Result<(), AuthorityKeyError> {
            if digest.iter().all(|value| *value == 0) {
                return Err(AuthorityKeyError("authority_signature_digest_zero"));
            }
            if !signature_is_canonical(signature) {
                return Err(AuthorityKeyError("authority_signature_scalar_invalid"));
            }
            check(
                unsafe {
                    NCryptVerifySignature(
                        self.0,
                        null(),
                        digest.as_ptr(),
                        digest.len() as u32,
                        signature.as_ptr(),
                        signature.len() as u32,
                        NCRYPT_SILENT_FLAG,
                    )
                },
                "authority_signature_verification_failed",
            )
        }

        fn string_property(&self, property: PCWSTR) -> Result<String, AuthorityKeyError> {
            let bytes =
                self.property_bytes(property, MAX_STRING_PROPERTY_BYTES, NCRYPT_SILENT_FLAG)?;
            if bytes.len() < 2 || bytes.len() % 2 != 0 {
                return Err(AuthorityKeyError("authority_key_property_shape_invalid"));
            }
            let mut words = bytes
                .chunks_exact(2)
                .map(|word| u16::from_le_bytes([word[0], word[1]]))
                .collect::<Vec<_>>();
            if words.pop() != Some(0) || words.contains(&0) {
                return Err(AuthorityKeyError("authority_key_property_shape_invalid"));
            }
            String::from_utf16(&words)
                .map_err(|_| AuthorityKeyError("authority_key_property_shape_invalid"))
        }

        fn security_descriptor_sddl(&self) -> Result<String, AuthorityKeyError> {
            let bytes = self.property_bytes(
                NCRYPT_SECURITY_DESCR_PROPERTY,
                MAX_SECURITY_DESCRIPTOR_BYTES,
                SECURITY_INFORMATION | NCRYPT_SILENT_FLAG,
            )?;
            if bytes.is_empty() {
                return Err(AuthorityKeyError(
                    "authority_key_security_descriptor_invalid",
                ));
            }
            let mut output: PWSTR = null_mut();
            let mut output_length = 0u32;
            let converted = unsafe {
                ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    bytes.as_ptr() as PSECURITY_DESCRIPTOR,
                    SDDL_REVISION_1,
                    SECURITY_INFORMATION,
                    &mut output,
                    &mut output_length,
                )
            };
            if converted == 0 || output.is_null() || output_length == 0 {
                if !output.is_null() {
                    unsafe {
                        LocalFree(output as _);
                    }
                }
                return Err(AuthorityKeyError(
                    "authority_key_security_descriptor_invalid",
                ));
            }
            let result = unsafe {
                let mut words = std::slice::from_raw_parts(output, output_length as usize).to_vec();
                if words.last() == Some(&0) {
                    words.pop();
                }
                let value = if words.contains(&0) {
                    Err(AuthorityKeyError(
                        "authority_key_security_descriptor_invalid",
                    ))
                } else {
                    String::from_utf16(&words)
                        .map_err(|_| AuthorityKeyError("authority_key_security_descriptor_invalid"))
                };
                LocalFree(output as _);
                value
            }?;
            Ok(result)
        }

        fn public_key(&self) -> Result<(u32, u32, [u8; PUBLIC_KEY_SIZE]), AuthorityKeyError> {
            let mut required = 0u32;
            let status = unsafe {
                NCryptExportKey(
                    self.0,
                    0,
                    BCRYPT_ECCPUBLIC_BLOB,
                    null(),
                    null_mut(),
                    0,
                    &mut required,
                    NCRYPT_SILENT_FLAG,
                )
            };
            check(status, "authority_key_public_readback_failed")?;
            if required != 72 {
                return Err(AuthorityKeyError("authority_key_public_key_invalid"));
            }
            let mut blob = [0u8; 72];
            let mut written = 0u32;
            let status = unsafe {
                NCryptExportKey(
                    self.0,
                    0,
                    BCRYPT_ECCPUBLIC_BLOB,
                    null(),
                    blob.as_mut_ptr(),
                    blob.len() as u32,
                    &mut written,
                    NCRYPT_SILENT_FLAG,
                )
            };
            check(status, "authority_key_public_readback_failed")?;
            if written != blob.len() as u32 {
                return Err(AuthorityKeyError("authority_key_public_key_invalid"));
            }
            let magic = u32::from_le_bytes(blob[..4].try_into().unwrap_or_default());
            let coordinate_size = u32::from_le_bytes(blob[4..8].try_into().unwrap_or_default());
            if magic != BCRYPT_ECDSA_PUBLIC_P256_MAGIC || coordinate_size != P256_COORDINATE_SIZE {
                return Err(AuthorityKeyError("authority_key_public_key_invalid"));
            }
            let mut public_key = [0u8; PUBLIC_KEY_SIZE];
            public_key[0] = 0x04;
            public_key[1..].copy_from_slice(&blob[8..]);
            Ok((magic, coordinate_size, public_key))
        }

        fn property_bytes(
            &self,
            property: PCWSTR,
            maximum_size: u32,
            flags: u32,
        ) -> Result<Vec<u8>, AuthorityKeyError> {
            let mut required = 0u32;
            let status =
                unsafe { NCryptGetProperty(self.0, property, null_mut(), 0, &mut required, flags) };
            check(status, "authority_key_property_read_failed")?;
            if required == 0 || required > maximum_size {
                return Err(AuthorityKeyError("authority_key_property_shape_invalid"));
            }
            let mut bytes = vec![0u8; required as usize];
            let mut written = 0u32;
            let status = unsafe {
                NCryptGetProperty(
                    self.0,
                    property,
                    bytes.as_mut_ptr(),
                    required,
                    &mut written,
                    flags,
                )
            };
            check(status, "authority_key_property_read_failed")?;
            if written != required {
                return Err(AuthorityKeyError("authority_key_property_shape_invalid"));
            }
            Ok(bytes)
        }
    }

    impl Drop for Key {
        fn drop(&mut self) {
            if self.0 != 0 {
                unsafe {
                    NCryptFreeObject(self.0);
                }
                self.0 = 0;
            }
        }
    }

    pub(super) fn inspect_existing_machine_key(
        policy: &AuthorityKeyPolicy,
    ) -> Result<AuthorityKeyReadback, AuthorityKeyError> {
        let provider = Provider::open()?;
        let key = match provider.open_existing_machine_key(policy.key_name())? {
            Some(key) => key,
            None => return validate_snapshot(policy, AuthorityKeySnapshot::Absent),
        };
        validate_snapshot(
            policy,
            AuthorityKeySnapshot::Present(Box::new(key.snapshot()?)),
        )
    }

    pub struct ProvisionedAuthorityKey {
        key: Key,
        readback: VerifiedAuthorityKeyReadback,
    }

    pub struct OpenedAuthorityKey {
        key: Key,
        readback: VerifiedAuthorityKeyReadback,
    }

    impl OpenedAuthorityKey {
        pub fn readback(&self) -> &VerifiedAuthorityKeyReadback {
            &self.readback
        }

        /// Revalidates every security and cryptographic property through the
        /// retained key handle. No key-name lookup or replacement handle is
        /// permitted after the authenticated boundary has adopted this key.
        pub(crate) fn verify_current(
            &self,
            policy: &AuthorityKeyPolicy,
        ) -> Result<(), AuthorityKeyError> {
            let current = match validate_snapshot(
                policy,
                AuthorityKeySnapshot::Present(Box::new(self.key.snapshot()?)),
            )? {
                AuthorityKeyReadback::Verified(value) => value,
                AuthorityKeyReadback::Absent { .. } => {
                    return Err(AuthorityKeyError("authority_key_missing"))
                }
            };
            if current != self.readback {
                return Err(AuthorityKeyError("authority_key_readback_changed"));
            }
            Ok(())
        }

        /// Signs with the already-opened, policy-verified machine key.
        ///
        /// This deliberately has no path/name based fallback: the protected
        /// service boundary must retain this unique handle and revalidate its
        /// surrounding FinalCommit boundary before each use.
        pub(crate) fn sign_digest(
            &self,
            digest: &[u8; 32],
        ) -> Result<[u8; P256_SIGNATURE_SIZE], AuthorityKeyError> {
            self.key.sign_digest(digest)
        }

        pub fn verify_digest_signature(
            &self,
            digest: &[u8; 32],
            signature: &[u8; P256_SIGNATURE_SIZE],
        ) -> Result<(), AuthorityKeyError> {
            self.key.verify_digest_signature(digest, signature)
        }
    }

    pub(super) fn open_verified_machine_key(
        policy: &AuthorityKeyPolicy,
    ) -> Result<OpenedAuthorityKey, AuthorityKeyError> {
        let provider = Provider::open()?;
        let key = provider
            .open_existing_machine_key(policy.key_name())?
            .ok_or(AuthorityKeyError("authority_key_missing"))?;
        let readback = match validate_snapshot(
            policy,
            AuthorityKeySnapshot::Present(Box::new(key.snapshot()?)),
        )? {
            AuthorityKeyReadback::Verified(value) => value,
            AuthorityKeyReadback::Absent { .. } => {
                return Err(AuthorityKeyError("authority_key_missing"))
            }
        };
        Ok(OpenedAuthorityKey { key, readback })
    }

    impl ProvisionedAuthorityKey {
        pub fn readback(&self) -> &VerifiedAuthorityKeyReadback {
            &self.readback
        }

        pub fn sign_digest(&self, digest: &[u8; 32]) -> Result<[u8; 64], AuthorityKeyError> {
            self.key.sign_digest(digest)
        }
    }

    pub(super) fn provision_new_machine_key(
        authority_generation_digest: [u8; DIGEST_SIZE],
        service_sid: &str,
    ) -> Result<ProvisionedAuthorityKey, AuthorityKeyError> {
        let key_name = derive_machine_key_name(&authority_generation_digest)?;
        validate_service_sid(service_sid)?;
        let security_descriptor = format!("O:SYG:SYD:P(A;;GA;;;SY)(A;;GA;;;{service_sid})");
        let provider = Provider::open()?;
        if provider.open_existing_machine_key(&key_name)?.is_some() {
            return Err(AuthorityKeyError("authority_key_already_exists"));
        }
        let pending = provider.create_new_machine_key(&key_name, &security_descriptor)?;
        let reopened = provider
            .open_existing_machine_key(&key_name)?
            .ok_or(AuthorityKeyError("authority_key_stable_reopen_failed"))?;
        let snapshot = reopened.snapshot()?;
        let public_key = snapshot
            .public_key_sec1
            .as_deref()
            .ok_or(AuthorityKeyError("authority_key_public_key_missing"))?;
        let expected_signer_key_id: [u8; DIGEST_SIZE] = Sha256::digest(public_key).into();
        let policy = AuthorityKeyPolicy::new(
            authority_generation_digest,
            expected_signer_key_id,
            service_sid,
        )?;
        let readback =
            match validate_snapshot(&policy, AuthorityKeySnapshot::Present(Box::new(snapshot)))? {
                AuthorityKeyReadback::Verified(value) => value,
                AuthorityKeyReadback::Absent { .. } => {
                    return Err(AuthorityKeyError("authority_key_finalize_failed"))
                }
            };
        let key = pending.commit();
        Ok(ProvisionedAuthorityKey { key, readback })
    }

    pub(super) fn normalize_low_s(signature: &mut [u8; P256_SIGNATURE_SIZE]) {
        let s: &mut [u8; 32] = (&mut signature[32..])
            .try_into()
            .expect("fixed P-256 signature shape");
        if *s <= P256_HALF_ORDER {
            return;
        }
        let mut borrow = 0u16;
        for index in (0..32).rev() {
            let left = P256_ORDER[index] as u16;
            let right = s[index] as u16 + borrow;
            if left >= right {
                s[index] = (left - right) as u8;
                borrow = 0;
            } else {
                s[index] = (left + 256 - right) as u8;
                borrow = 1;
            }
        }
    }

    pub(super) fn signature_is_canonical(signature: &[u8; P256_SIGNATURE_SIZE]) -> bool {
        let r: [u8; 32] = signature[..32]
            .try_into()
            .expect("fixed P-256 signature shape");
        let s: [u8; 32] = signature[32..]
            .try_into()
            .expect("fixed P-256 signature shape");
        r.iter().any(|value| *value != 0)
            && r < P256_ORDER
            && s.iter().any(|value| *value != 0)
            && s <= P256_HALF_ORDER
    }

    fn wide_null(value: &str) -> Result<Vec<u16>, AuthorityKeyError> {
        if value.is_empty() || value.encode_utf16().any(|word| word == 0) {
            return Err(AuthorityKeyError("authority_key_name_invalid"));
        }
        Ok(value.encode_utf16().chain(std::iter::once(0)).collect())
    }

    fn check(status: i32, code: &'static str) -> Result<(), AuthorityKeyError> {
        if status == 0 {
            Ok(())
        } else {
            Err(AuthorityKeyError(code))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::Cell, rc::Rc};

    const SERVICE_SID: &str = "S-1-5-80-1-2-3-4-5";

    fn public_key() -> [u8; PUBLIC_KEY_SIZE] {
        let mut value = [0u8; PUBLIC_KEY_SIZE];
        value[0] = 0x04;
        for (index, byte) in value[1..].iter_mut().enumerate() {
            *byte = (index + 1) as u8;
        }
        value
    }

    fn policy() -> AuthorityKeyPolicy {
        let public_key = public_key();
        AuthorityKeyPolicy::new(
            [0x11; DIGEST_SIZE],
            Sha256::digest(public_key).into(),
            SERVICE_SID,
        )
        .expect("valid policy")
    }

    #[derive(Clone)]
    struct MockPendingKey {
        delete_count: Rc<Cell<u32>>,
    }

    fn delete_mock_pending_key(key: &mut MockPendingKey) {
        key.delete_count.set(key.delete_count.get() + 1);
    }

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum ProvisionFault {
        Configure,
        Finalize,
        StableReopen,
        ExactReadback,
        None,
    }

    fn simulate_pending_key_provision(
        fault: ProvisionFault,
        delete_count: Rc<Cell<u32>>,
    ) -> Result<(), ()> {
        let pending =
            PendingCreatedResource::new(MockPendingKey { delete_count }, delete_mock_pending_key);
        if fault == ProvisionFault::Configure {
            return Err(());
        }
        let _configured = pending.value();
        if fault == ProvisionFault::Finalize {
            return Err(());
        }
        if fault == ProvisionFault::StableReopen {
            return Err(());
        }
        if fault == ProvisionFault::ExactReadback {
            return Err(());
        }
        let _accepted = pending.commit();
        Ok(())
    }

    #[test]
    fn pending_key_guard_deletes_once_at_every_pre_acceptance_failure() {
        for fault in [
            ProvisionFault::Configure,
            ProvisionFault::Finalize,
            ProvisionFault::StableReopen,
            ProvisionFault::ExactReadback,
        ] {
            let delete_count = Rc::new(Cell::new(0));
            assert!(simulate_pending_key_provision(fault, delete_count.clone()).is_err());
            assert_eq!(delete_count.get(), 1);
        }

        let delete_count = Rc::new(Cell::new(0));
        simulate_pending_key_provision(ProvisionFault::None, delete_count.clone())
            .expect("accepted key disarms pending deletion");
        assert_eq!(delete_count.get(), 0);
    }

    fn valid_properties(policy: &AuthorityKeyPolicy) -> AuthorityKeyPropertySnapshot {
        AuthorityKeyPropertySnapshot {
            open_flags: OPEN_EXISTING_FLAGS,
            name: Some(policy.key_name().to_string()),
            key_type: Some(MACHINE_KEY_FLAG),
            algorithm: Some(EXPECTED_ALGORITHM.to_string()),
            algorithm_group: Some(EXPECTED_ALGORITHM_GROUP.to_string()),
            key_length_bits: Some(P256_KEY_LENGTH_BITS),
            key_usage: Some(SIGN_ONLY_USAGE),
            export_policy: Some(NO_EXPORT_POLICY),
            public_blob_magic: Some(P256_PUBLIC_BLOB_MAGIC),
            public_coordinate_size: Some(P256_COORDINATE_SIZE),
            public_key_sec1: Some(public_key().to_vec()),
            security_descriptor_sddl: Some(policy.expected_security_descriptor_sddl().to_string()),
            unrecognized_properties: Vec::new(),
        }
    }

    fn validate_properties(
        policy: &AuthorityKeyPolicy,
        properties: AuthorityKeyPropertySnapshot,
    ) -> Result<AuthorityKeyReadback, AuthorityKeyError> {
        validate_snapshot(policy, AuthorityKeySnapshot::Present(Box::new(properties)))
    }

    #[test]
    fn key_name_requires_nonzero_generation_and_is_deterministic() {
        assert_eq!(
            derive_machine_key_name(&[0; DIGEST_SIZE])
                .expect_err("zero generation must fail")
                .code(),
            "authority_generation_digest_zero"
        );
        let name = derive_machine_key_name(&[0x11; DIGEST_SIZE]).expect("valid digest");
        assert_eq!(
            name,
            format!("{AUTHORITY_KEY_NAME_PREFIX}{}", "11".repeat(DIGEST_SIZE))
        );
        assert_ne!(
            name,
            derive_machine_key_name(&[0x12; DIGEST_SIZE]).expect("other digest")
        );
    }

    #[test]
    fn policy_rejects_zero_key_id_and_noncanonical_service_sid() {
        assert_eq!(
            AuthorityKeyPolicy::new([1; DIGEST_SIZE], [0; DIGEST_SIZE], SERVICE_SID)
                .expect_err("zero key id must fail")
                .code(),
            "authority_signer_key_id_zero"
        );
        for invalid in [
            "S-1-5-18",
            "S-1-5-80-1-2-3-4",
            "S-1-5-80-01-2-3-4-5",
            "S-1-5-80-1-2-3-4-4294967296",
            "S-1-5-80-0-0-0-0-0",
            "S-1-5-80-1-2-3-4-5)(A;;GA;;;WD",
        ] {
            assert_eq!(
                AuthorityKeyPolicy::new([1; DIGEST_SIZE], [2; DIGEST_SIZE], invalid)
                    .expect_err("invalid service sid must fail")
                    .code(),
                "authority_service_sid_invalid"
            );
        }
    }

    #[test]
    fn absent_key_is_a_safe_readback_state() {
        let policy = policy();
        let readback = validate_snapshot(&policy, AuthorityKeySnapshot::Absent)
            .expect("absence is not a read failure");
        assert!(readback.is_absent());
        assert_eq!(readback.key_name(), policy.key_name());
    }

    #[test]
    fn exact_snapshot_is_verified() {
        let policy = policy();
        let readback = validate_properties(&policy, valid_properties(&policy))
            .expect("exact snapshot must verify");
        let AuthorityKeyReadback::Verified(readback) = readback else {
            panic!("expected verified readback");
        };
        assert_eq!(readback.key_name(), policy.key_name());
        assert_eq!(readback.signer_key_id(), policy.expected_signer_key_id());
        assert_eq!(readback.public_key_sec1(), &public_key());
        assert_eq!(readback.signer_key_id_hex().len(), 64);
    }

    #[test]
    fn user_scope_and_nonexact_open_flags_fail_closed() {
        let policy = policy();
        let mut properties = valid_properties(&policy);
        properties.key_type = Some(0);
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("user key must fail")
                .code(),
            "authority_key_not_machine_scoped"
        );

        let mut properties = valid_properties(&policy);
        properties.open_flags = SILENT_FLAG;
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("missing machine flag must fail")
                .code(),
            "authority_key_open_flags_mismatch"
        );

        let mut properties = valid_properties(&policy);
        properties.open_flags = OPEN_EXISTING_FLAGS | 0x1;
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("unknown open flag must fail")
                .code(),
            "authority_key_open_flags_mismatch"
        );
    }

    #[test]
    fn exportable_or_wrong_usage_keys_fail_closed() {
        let policy = policy();
        for export_policy in [1, 2, 4, u32::MAX] {
            let mut properties = valid_properties(&policy);
            properties.export_policy = Some(export_policy);
            assert_eq!(
                validate_properties(&policy, properties)
                    .expect_err("exportable key must fail")
                    .code(),
                "authority_key_exportable"
            );
        }
        for usage in [0, 1, SIGN_ONLY_USAGE | 1, u32::MAX] {
            let mut properties = valid_properties(&policy);
            properties.key_usage = Some(usage);
            assert_eq!(
                validate_properties(&policy, properties)
                    .expect_err("non-sign-only key must fail")
                    .code(),
                "authority_key_usage_not_sign_only"
            );
        }
    }

    #[test]
    fn public_key_and_key_id_mismatches_fail_closed() {
        let policy = policy();
        let mut properties = valid_properties(&policy);
        properties.public_key_sec1.as_mut().expect("public key")[64] ^= 0x01;
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("key substitution must fail")
                .code(),
            "authority_signer_key_id_mismatch"
        );

        let mut properties = valid_properties(&policy);
        properties.public_blob_magic = Some(0);
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("wrong blob magic must fail")
                .code(),
            "authority_key_public_blob_magic_mismatch"
        );

        let mut properties = valid_properties(&policy);
        properties.public_key_sec1 = Some(vec![0x04; 64]);
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("wrong public key length must fail")
                .code(),
            "authority_key_public_key_invalid"
        );
    }

    #[test]
    fn wrong_acl_and_unknown_properties_fail_closed() {
        let policy = policy();
        let mut properties = valid_properties(&policy);
        properties.security_descriptor_sddl =
            Some("O:SYG:SYD:P(A;;GA;;;SY)(A;;GA;;;BA)".to_string());
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("wrong acl must fail")
                .code(),
            "authority_key_security_descriptor_mismatch"
        );

        let mut properties = valid_properties(&policy);
        properties
            .unrecognized_properties
            .push("futureProperty".to_string());
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("unknown property must fail")
                .code(),
            "authority_key_unrecognized_property_present"
        );
    }

    #[test]
    fn missing_required_properties_fail_closed() {
        let policy = policy();

        let mut properties = valid_properties(&policy);
        properties.name = None;
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("missing name must fail")
                .code(),
            "authority_key_name_missing"
        );

        let mut properties = valid_properties(&policy);
        properties.algorithm = None;
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("missing algorithm must fail")
                .code(),
            "authority_key_algorithm_missing"
        );

        let mut properties = valid_properties(&policy);
        properties.key_usage = None;
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("missing usage must fail")
                .code(),
            "authority_key_usage_missing"
        );

        let mut properties = valid_properties(&policy);
        properties.export_policy = None;
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("missing export policy must fail")
                .code(),
            "authority_key_export_policy_missing"
        );

        let mut properties = valid_properties(&policy);
        properties.security_descriptor_sddl = None;
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("missing acl must fail")
                .code(),
            "authority_key_security_descriptor_missing"
        );
    }

    #[test]
    fn name_algorithm_and_shape_drift_fail_closed() {
        let policy = policy();

        let mut properties = valid_properties(&policy);
        properties.name = Some("different".to_string());
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("wrong name must fail")
                .code(),
            "authority_key_name_mismatch"
        );

        let mut properties = valid_properties(&policy);
        properties.algorithm = Some("ECDH_P256".to_string());
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("wrong algorithm must fail")
                .code(),
            "authority_key_algorithm_mismatch"
        );

        let mut properties = valid_properties(&policy);
        properties.algorithm_group = Some("ECDH".to_string());
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("wrong algorithm group must fail")
                .code(),
            "authority_key_algorithm_group_mismatch"
        );

        let mut properties = valid_properties(&policy);
        properties.key_length_bits = Some(384);
        assert_eq!(
            validate_properties(&policy, properties)
                .expect_err("wrong key length must fail")
                .code(),
            "authority_key_length_mismatch"
        );
    }

    #[cfg(windows)]
    #[test]
    fn real_machine_probe_is_read_only_and_accepts_absence() {
        let generation = Sha256::digest(b"vrcforge-authority-key-readonly-absence-probe").into();
        let policy = AuthorityKeyPolicy::new(generation, [0x5a; DIGEST_SIZE], SERVICE_SID)
            .expect("valid read-only probe policy");
        let first = inspect_existing_machine_key(&policy).expect("read-only probe");
        let second = inspect_existing_machine_key(&policy).expect("repeat read-only probe");
        assert!(first.is_absent());
        assert_eq!(first, second);
    }

    #[cfg(windows)]
    #[test]
    fn p256_signature_normalization_is_low_s_and_idempotent() {
        let mut signature = [0u8; 64];
        signature[..32].fill(0x31);
        signature[32..].copy_from_slice(&windows::P256_ORDER);
        signature[63] -= 1;
        windows::normalize_low_s(&mut signature);
        let normalized: [u8; 32] = signature[32..].try_into().unwrap();
        assert!(normalized <= windows::P256_HALF_ORDER);
        let once = signature;
        windows::normalize_low_s(&mut signature);
        assert_eq!(signature, once);
    }

    #[cfg(windows)]
    #[test]
    fn p256_signature_verifier_rejects_zero_out_of_range_and_high_s_scalars() {
        let mut signature = [0u8; 64];
        signature[31] = 1;
        signature[63] = 1;
        assert!(windows::signature_is_canonical(&signature));

        signature[..32].fill(0);
        assert!(!windows::signature_is_canonical(&signature));
        signature[..32].copy_from_slice(&windows::P256_ORDER);
        assert!(!windows::signature_is_canonical(&signature));

        signature[..32].fill(0);
        signature[31] = 1;
        signature[32..].copy_from_slice(&windows::P256_ORDER);
        signature[63] -= 1;
        assert!(!windows::signature_is_canonical(&signature));
    }
}
