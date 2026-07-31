//! Protected, content-addressed storage for durable runtime evidence payloads.
//!
//! The append-only ledger binds one immutable object with one fixed-size frame.
//! This module never accepts a caller-selected child path: every name is derived
//! from the authenticated namespace, ticket, run, kind, and content digest.

use sha2::{Digest as _, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    path::{Component, Path, PathBuf},
};

#[cfg(any(test, unix))]
use std::fs;

#[cfg(unix)]
use std::os::unix::fs::MetadataExt as UnixMetadataExt;

#[cfg(windows)]
use std::{
    mem::{size_of, zeroed},
    os::windows::{
        fs::MetadataExt as WindowsMetadataExt,
        io::{AsRawHandle, FromRawHandle, RawHandle},
    },
    ptr, slice,
};

#[cfg(windows)]
use crate::primitive_evidence_authority_install::{
    finalizer_commit_store_windows::VerifiedPublishedProtectedBlobNamespaceAdoption,
    security_policy::{
        RUNTIME_BLOB_FILE_AUTHORITY_ACCESS, RUNTIME_BLOB_FILE_CLEANUP_ACCESS,
        RUNTIME_BLOB_FILE_READ_ACCESS, RUNTIME_BLOB_FILE_SDDL,
    },
};

#[cfg(all(windows, test))]
use std::os::windows::ffi::OsStrExt;

#[cfg(windows)]
use windows_sys::{
    Wdk::{
        Foundation::{NtQueryObject, ObjectBasicInformation, OBJECT_ATTRIBUTES},
        Storage::FileSystem::{
            FileDispositionInformationEx, FileNamesInformation, NtCreateFile, NtFlushBuffersFile,
            NtQueryDirectoryFile, NtSetInformationFile, FILE_CREATE, FILE_DISPOSITION_DELETE,
            FILE_DISPOSITION_INFORMATION_EX, FILE_NON_DIRECTORY_FILE, FILE_OPEN,
            FILE_OPEN_REPARSE_POINT, FILE_SYNCHRONOUS_IO_NONALERT, FILE_WRITE_THROUGH,
        },
    },
    Win32::{
        Foundation::{LocalFree, INVALID_HANDLE_VALUE, UNICODE_STRING},
        Security::{
            Authorization::{
                ConvertSecurityDescriptorToStringSecurityDescriptorW,
                ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo,
                SDDL_REVISION_1, SE_FILE_OBJECT,
            },
            DACL_SECURITY_INFORMATION, GROUP_SECURITY_INFORMATION, LABEL_SECURITY_INFORMATION,
            OWNER_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR,
        },
        Storage::FileSystem::{
            FileIdInfo, FileStandardInfo, GetFileInformationByHandleEx, FILE_ATTRIBUTE_DIRECTORY,
            FILE_ATTRIBUTE_NORMAL, FILE_ATTRIBUTE_REPARSE_POINT, FILE_ID_INFO, FILE_STANDARD_INFO,
        },
        System::IO::IO_STATUS_BLOCK,
    },
};

#[cfg(all(windows, test))]
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, FILE_ADD_FILE, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES, FILE_TRAVERSE, OPEN_EXISTING, READ_CONTROL,
    SYNCHRONIZE,
};

pub(crate) type BlobDigest = [u8; 32];

pub(crate) const PROTECTED_BLOB_SCHEMA_VERSION: u16 = 1;
pub(crate) const PROTECTED_BLOB_HEADER_SIZE: usize = 344;
pub(crate) const PROTECTED_BLOB_IO_CHUNK_SIZE: usize = 64 * 1024;
pub(crate) const MAX_PROTECTED_BLOB_NAMESPACE_OBJECT_COUNT: u64 = 3 * 1024;
pub(crate) const MAX_PROTECTED_BLOB_NAMESPACE_STORED_BYTES: u64 = 272 * 1024 * 1024;

const ZERO_DIGEST: BlobDigest = [0; 32];
const BLOB_MAGIC: &[u8; 8] = b"VRCBLB01";
const BLOB_HEADER_DOMAIN: &[u8] = b"vrcforge-protected-blob-header-v1\0";
const BLOB_ADDRESS_DOMAIN: &[u8] = b"vrcforge-protected-blob-address-v1\0";
const BLOB_BINDING_DOMAIN: &[u8] = b"vrcforge-protected-blob-binding-v1\0";
const BLOB_NAMESPACE_DOMAIN: &[u8] = b"vrcforge-protected-blob-namespace-v1\0";
const BLOB_OBJECT_IDENTITY_DOMAIN: &[u8] = b"vrcforge-protected-blob-object-v1\0";
const BLOB_CLEANUP_DOMAIN: &[u8] = b"vrcforge-protected-blob-cleanup-v1\0";
#[cfg(test)]
const TEST_SECURITY_PROBE_NAME: &str = ".vrcforge-blob-security-probe";
const FILE_CREATED_INFORMATION: usize = 2;
const FILE_OPENED_INFORMATION: usize = 1;
const STATUS_NO_SUCH_FILE: i32 = 0xc000_000fu32 as i32;
const STATUS_OBJECT_NAME_NOT_FOUND: i32 = 0xc000_0034u32 as i32;
const STATUS_OBJECT_NAME_COLLISION: i32 = 0xc000_0035u32 as i32;
const STATUS_OBJECT_PATH_NOT_FOUND: i32 = 0xc000_003au32 as i32;
const BLOB_FILE_CREATE_ACCESS: u32 = 0x0013_0083;
const BLOB_FILE_READ_ACCESS: u32 = 0x0012_0081;
const BLOB_FILE_CLEANUP_ACCESS: u32 = 0x0013_0081;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProtectedBlobError(String);

impl ProtectedBlobError {
    fn new(code: impl Into<String>) -> Self {
        Self(code.into())
    }

    pub(crate) fn code(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ProtectedBlobError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ProtectedBlobError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
#[repr(u8)]
pub(crate) enum ProtectedBlobKind {
    VerifiedResult = 1,
    ResultCommit = 2,
    Projection = 3,
}

impl ProtectedBlobKind {
    pub(crate) fn from_u8(value: u8) -> Result<Self, ProtectedBlobError> {
        match value {
            1 => Ok(Self::VerifiedResult),
            2 => Ok(Self::ResultCommit),
            3 => Ok(Self::Projection),
            _ => Err(ProtectedBlobError::new("protected_blob_kind_invalid")),
        }
    }

    pub(crate) const fn maximum_content_size(self) -> usize {
        match self {
            Self::VerifiedResult => 64 * 1024 + 512 * 1024 + 512,
            Self::ResultCommit => 64 * 1024,
            Self::Projection => 10 * 1024 * 1024 + 64 * 1024,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ProtectedBlobBindingContext {
    kind: ProtectedBlobKind,
    ticket_digest: BlobDigest,
    run_binding_digest: BlobDigest,
    prepared_source_digest: BlobDigest,
    policy_snapshot_digest: BlobDigest,
    recovery_bundle_digest: BlobDigest,
}

impl ProtectedBlobBindingContext {
    pub(crate) fn new(
        kind: ProtectedBlobKind,
        ticket_digest: BlobDigest,
        run_binding_digest: BlobDigest,
        prepared_source_digest: BlobDigest,
        policy_snapshot_digest: BlobDigest,
        recovery_bundle_digest: BlobDigest,
    ) -> Result<Self, ProtectedBlobError> {
        if [
            ticket_digest,
            run_binding_digest,
            prepared_source_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
        ]
        .iter()
        .any(is_zero)
        {
            return Err(ProtectedBlobError::new("protected_blob_context_invalid"));
        }
        Ok(Self {
            kind,
            ticket_digest,
            run_binding_digest,
            prepared_source_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
        })
    }

    pub(crate) const fn ticket_digest(&self) -> &BlobDigest {
        &self.ticket_digest
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProtectedBlobNamespaceDescriptor {
    generation_digest: BlobDigest,
    ledger_identity_digest: BlobDigest,
    root_identity_digest: BlobDigest,
    root_security_digest: BlobDigest,
    file_security_digest: BlobDigest,
    namespace_seal_digest: BlobDigest,
    file_create_access: u32,
    file_read_access: u32,
    file_cleanup_access: u32,
    source_digest: BlobDigest,
}

/// Non-clone service-write namespace handoff reserved for the authenticated
/// FinalCommit/bootstrap join. It carries a held directory object plus the
/// exact identity/security readback; it never names an arbitrary child path.
pub(crate) struct AuthenticatedProtectedBlobNamespace {
    root_path: PathBuf,
    root: File,
    descriptor: ProtectedBlobNamespaceDescriptor,
    #[cfg(test)]
    unsecured_test: bool,
}

impl AuthenticatedProtectedBlobNamespace {
    #[cfg(windows)]
    pub(crate) fn from_verified_final_commit(
        adoption: VerifiedPublishedProtectedBlobNamespaceAdoption,
        generation_digest: BlobDigest,
        ledger_identity_digest: BlobDigest,
    ) -> Result<Self, ProtectedBlobError> {
        if is_zero(&generation_digest) || is_zero(&ledger_identity_digest) {
            return Err(ProtectedBlobError::new(
                "protected_blob_namespace_binding_invalid",
            ));
        }
        adoption.consume_with(|root, root_path, projection| {
            projection
                .validate()
                .map_err(|_| ProtectedBlobError::new("protected_blob_namespace_seal_invalid"))?;
            if projection.generation_sha256() != generation_digest
                || projection.file_create_access() != RUNTIME_BLOB_FILE_AUTHORITY_ACCESS
                || projection.file_read_access() != RUNTIME_BLOB_FILE_READ_ACCESS
                || projection.file_cleanup_access() != RUNTIME_BLOB_FILE_CLEANUP_ACCESS
            {
                return Err(ProtectedBlobError::new(
                    "protected_blob_namespace_binding_invalid",
                ));
            }
            validate_root_path(&root_path)?;
            let root = File::from(root);
            let observed = stable_blob_identity(&root, true)?;
            if observed.volume_serial != projection.volume_serial()
                || observed.file_id != projection.file_id()
                || observed.link_count != projection.link_count()
                || observed.attributes != projection.attributes()
                || observed.byte_length != projection.byte_length()
            {
                return Err(ProtectedBlobError::new(
                    "protected_blob_root_identity_changed",
                ));
            }
            let root_identity_digest = stable_blob_identity_digest(b"root", &observed);
            let root_security_digest = security_digest(&root)?;
            if root_security_digest != projection.final_security_sha256() {
                return Err(ProtectedBlobError::new(
                    "protected_blob_root_security_changed",
                ));
            }
            let descriptor = ProtectedBlobNamespaceDescriptor::new(
                generation_digest,
                ledger_identity_digest,
                root_identity_digest,
                root_security_digest,
                projection.file_security_sha256(),
                projection.seal_sha256(),
                projection.file_create_access(),
                projection.file_read_access(),
                projection.file_cleanup_access(),
            )?;
            let value = Self {
                root_path,
                root,
                descriptor,
                #[cfg(test)]
                unsecured_test: false,
            };
            value.verify_held_root()?;
            Ok(value)
        })
    }

    fn verify_held_root(&self) -> Result<(), ProtectedBlobError> {
        let observed = stable_blob_identity(&self.root, true)?;
        if stable_blob_identity_digest(b"root", &observed) != self.descriptor.root_identity_digest
            || security_digest(&self.root)? != self.descriptor.root_security_digest
        {
            return Err(ProtectedBlobError::new(
                "protected_blob_root_identity_changed",
            ));
        }
        Ok(())
    }

    #[cfg(test)]
    #[allow(clippy::too_many_arguments)]
    fn from_test_readback(
        root_path: PathBuf,
        root: File,
        generation_digest: BlobDigest,
        ledger_identity_digest: BlobDigest,
        root_identity_digest: BlobDigest,
        root_security_digest: BlobDigest,
        file_security_digest: BlobDigest,
    ) -> Result<Self, ProtectedBlobError> {
        validate_root_path(&root_path)?;
        let descriptor = ProtectedBlobNamespaceDescriptor::new(
            generation_digest,
            ledger_identity_digest,
            root_identity_digest,
            root_security_digest,
            file_security_digest,
            root_identity_digest,
            BLOB_FILE_CREATE_ACCESS,
            BLOB_FILE_READ_ACCESS,
            BLOB_FILE_CLEANUP_ACCESS,
        )?;
        let observed_identity = stable_blob_identity(&root, true)?;
        if stable_blob_identity_digest(b"root", &observed_identity)
            != descriptor.root_identity_digest
            || security_digest(&root)? != descriptor.root_security_digest
        {
            return Err(ProtectedBlobError::new(
                "protected_blob_root_identity_changed",
            ));
        }
        Ok(Self {
            root_path,
            root,
            descriptor,
            unsecured_test: true,
        })
    }

    pub(crate) fn into_authority(self) -> Result<ProtectedBlobAuthority, ProtectedBlobError> {
        let authority = ProtectedBlobAuthority::from_authenticated_held_root(
            self.root_path,
            self.root,
            self.descriptor,
        )?;
        #[cfg(test)]
        let authority = {
            let mut authority = authority;
            authority.unsecured_test = self.unsecured_test;
            authority
        };
        Ok(authority)
    }

    #[cfg(test)]
    pub(crate) fn provision_unsecured_test(
        root_path: PathBuf,
        generation_digest: BlobDigest,
        ledger_identity_digest: BlobDigest,
    ) -> Result<Self, ProtectedBlobError> {
        let (authority, descriptor) = ProtectedBlobAuthority::provision_unsecured_test(
            root_path.clone(),
            generation_digest,
            ledger_identity_digest,
        )?;
        let ProtectedBlobAuthority { root, .. } = authority;
        Self::from_test_readback(
            root_path,
            root,
            descriptor.generation_digest,
            descriptor.ledger_identity_digest,
            descriptor.root_identity_digest,
            descriptor.root_security_digest,
            descriptor.file_security_digest,
        )
    }
}

impl ProtectedBlobNamespaceDescriptor {
    fn new(
        generation_digest: BlobDigest,
        ledger_identity_digest: BlobDigest,
        root_identity_digest: BlobDigest,
        root_security_digest: BlobDigest,
        file_security_digest: BlobDigest,
        namespace_seal_digest: BlobDigest,
        file_create_access: u32,
        file_read_access: u32,
        file_cleanup_access: u32,
    ) -> Result<Self, ProtectedBlobError> {
        if [
            generation_digest,
            ledger_identity_digest,
            root_identity_digest,
            root_security_digest,
            file_security_digest,
            namespace_seal_digest,
        ]
        .iter()
        .any(is_zero)
        {
            return Err(ProtectedBlobError::new("protected_blob_namespace_invalid"));
        }
        if file_create_access != BLOB_FILE_CREATE_ACCESS
            || file_read_access != BLOB_FILE_READ_ACCESS
            || file_cleanup_access != BLOB_FILE_CLEANUP_ACCESS
        {
            return Err(ProtectedBlobError::new("protected_blob_namespace_invalid"));
        }
        let mut digest = Sha256::new();
        digest.update(BLOB_NAMESPACE_DOMAIN);
        digest.update(PROTECTED_BLOB_SCHEMA_VERSION.to_be_bytes());
        digest.update(generation_digest);
        digest.update(ledger_identity_digest);
        digest.update(root_identity_digest);
        digest.update(root_security_digest);
        digest.update(file_security_digest);
        digest.update(namespace_seal_digest);
        digest.update(file_create_access.to_be_bytes());
        digest.update(file_read_access.to_be_bytes());
        digest.update(file_cleanup_access.to_be_bytes());
        let source_digest = digest.finalize().into();
        Ok(Self {
            generation_digest,
            ledger_identity_digest,
            root_identity_digest,
            root_security_digest,
            file_security_digest,
            namespace_seal_digest,
            file_create_access,
            file_read_access,
            file_cleanup_access,
            source_digest,
        })
    }

    pub(crate) const fn generation_digest(&self) -> &BlobDigest {
        &self.generation_digest
    }

    pub(crate) const fn ledger_identity_digest(&self) -> &BlobDigest {
        &self.ledger_identity_digest
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProtectedBlobReference {
    context: ProtectedBlobBindingContext,
    namespace_source_digest: BlobDigest,
    content_length: u64,
    content_digest: BlobDigest,
    object_length: u64,
    object_digest: BlobDigest,
    object_identity_digest: BlobDigest,
    object_security_digest: BlobDigest,
    address_digest: BlobDigest,
    binding_digest: BlobDigest,
    relative_name: String,
}

impl ProtectedBlobReference {
    pub(crate) const fn context(&self) -> &ProtectedBlobBindingContext {
        &self.context
    }

    pub(crate) const fn content_digest(&self) -> &BlobDigest {
        &self.content_digest
    }

    pub(crate) const fn binding_digest(&self) -> &BlobDigest {
        &self.binding_digest
    }

    pub(crate) const fn content_length(&self) -> u64 {
        self.content_length
    }

    pub(crate) const fn object_length(&self) -> u64 {
        self.object_length
    }

    pub(crate) fn relative_name(&self) -> &str {
        &self.relative_name
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ReopenedProtectedBlob {
    reference: ProtectedBlobReference,
    content: Vec<u8>,
}

impl ReopenedProtectedBlob {
    pub(crate) const fn reference(&self) -> &ProtectedBlobReference {
        &self.reference
    }

    #[cfg(test)]
    pub(crate) fn content(&self) -> &[u8] {
        &self.content
    }

    pub(crate) fn into_content(self) -> Vec<u8> {
        self.content
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProtectedBlobCleanupReceipt {
    relative_name: String,
    address_digest: BlobDigest,
    object_identity_digest: BlobDigest,
    object_security_digest: BlobDigest,
    observed_length: u64,
    observed_digest: BlobDigest,
    receipt_digest: BlobDigest,
}

impl ProtectedBlobCleanupReceipt {
    #[cfg(test)]
    pub(crate) fn relative_name(&self) -> &str {
        &self.relative_name
    }

    #[cfg(test)]
    pub(crate) const fn receipt_digest(&self) -> &BlobDigest {
        &self.receipt_digest
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct ProtectedBlobIoMetrics {
    pub(crate) create_count: u64,
    pub(crate) open_count: u64,
    pub(crate) write_call_count: u64,
    pub(crate) bytes_written: u64,
    pub(crate) blob_flush_count: u64,
    pub(crate) directory_flush_count: u64,
    pub(crate) read_call_count: u64,
    pub(crate) bytes_read: u64,
    pub(crate) cleanup_count: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct BlobHeader {
    context: ProtectedBlobBindingContext,
    generation_digest: BlobDigest,
    ledger_identity_digest: BlobDigest,
    namespace_source_digest: BlobDigest,
    content_length: u64,
    content_digest: BlobDigest,
}

#[derive(Debug, Clone, Copy)]
struct ProtectedBlobReadLimits {
    content_bytes: u64,
    object_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct StableBlobIdentity {
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
    #[cfg(unix)]
    link_count: u64,
    #[cfg(windows)]
    volume_serial: u64,
    #[cfg(windows)]
    file_id: [u8; 16],
    #[cfg(windows)]
    link_count: u32,
    #[cfg(windows)]
    attributes: u32,
    byte_length: u64,
    directory: bool,
}

struct HeldBlob {
    file: File,
    reference: ProtectedBlobReference,
}

#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ProtectedBlobTestFault {
    AfterCreateBeforeValidation,
    BeforeFirstWrite,
    AfterBytes(usize),
    BeforeFlush,
    AfterFlush,
    BeforeDirectoryFlush,
    AfterDirectoryFlush,
    AfterCleanupDisposition,
    AfterCleanupDeletes(usize),
    BeforeCleanupDirectoryFlush,
    AfterCleanupDirectoryFlush,
}

pub(crate) struct ProtectedBlobAuthority {
    root: File,
    descriptor: ProtectedBlobNamespaceDescriptor,
    held: BTreeMap<BlobDigest, HeldBlob>,
    cleanup_receipts: Vec<ProtectedBlobCleanupReceipt>,
    metrics: ProtectedBlobIoMetrics,
    namespace_object_count: u64,
    namespace_stored_bytes: u64,
    poisoned: bool,
    #[cfg(test)]
    fault: Option<ProtectedBlobTestFault>,
    #[cfg(test)]
    unsecured_test: bool,
}

impl fmt::Debug for ProtectedBlobAuthority {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ProtectedBlobAuthority")
            .field("source_digest", &hex_lower(&self.descriptor.source_digest))
            .field("held_count", &self.held.len())
            .field("cleanup_count", &self.cleanup_receipts.len())
            .finish()
    }
}

impl ProtectedBlobAuthority {
    /// Test/path constructor. Production must consume a separately provisioned
    /// held-root capability through `from_authenticated_held_root`.
    #[cfg(test)]
    pub(crate) fn provision_unsecured_test(
        root_path: PathBuf,
        generation_digest: BlobDigest,
        ledger_identity_digest: BlobDigest,
    ) -> Result<(Self, ProtectedBlobNamespaceDescriptor), ProtectedBlobError> {
        validate_root_path(&root_path)?;
        fs::create_dir(&root_path)
            .map_err(|_| ProtectedBlobError::new("protected_blob_root_provision_failed"))?;
        let root = open_root(&root_path)?;
        let root_identity = stable_blob_identity(&root, true)?;
        let root_identity_digest = stable_blob_identity_digest(b"root", &root_identity);
        let root_security_digest = security_digest(&root)?;
        let probe = create_relative_file(&root, TEST_SECURITY_PROBE_NAME, false)?
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_probe_collision"))?;
        let file_security_digest = security_digest(&probe)?;
        delete_relative_file(&root, TEST_SECURITY_PROBE_NAME, probe)?;
        flush_directory(&root)?;
        let descriptor = ProtectedBlobNamespaceDescriptor::new(
            generation_digest,
            ledger_identity_digest,
            root_identity_digest,
            root_security_digest,
            file_security_digest,
            root_identity_digest,
            BLOB_FILE_CREATE_ACCESS,
            BLOB_FILE_READ_ACCESS,
            BLOB_FILE_CLEANUP_ACCESS,
        )?;
        let mut authority =
            Self::from_authenticated_held_root(root_path, root, descriptor.clone())?;
        authority.unsecured_test = true;
        Ok((authority, descriptor))
    }

    #[cfg(test)]
    pub(crate) fn reopen_unsecured_test(
        root_path: PathBuf,
        descriptor: ProtectedBlobNamespaceDescriptor,
    ) -> Result<Self, ProtectedBlobError> {
        let root = open_root(&root_path)?;
        let mut authority = Self::from_authenticated_held_root(root_path, root, descriptor)?;
        authority.unsecured_test = true;
        Ok(authority)
    }

    /// Narrow future FinalCommit join: consumes one already-held, authenticated
    /// service-write namespace. It neither creates nor relaxes the generation root.
    fn from_authenticated_held_root(
        root_path: PathBuf,
        root: File,
        descriptor: ProtectedBlobNamespaceDescriptor,
    ) -> Result<Self, ProtectedBlobError> {
        validate_root_path(&root_path)?;
        let (namespace_object_count, namespace_stored_bytes) =
            scan_namespace_usage(&root, &descriptor)?;
        let value = Self {
            root,
            descriptor,
            held: BTreeMap::new(),
            cleanup_receipts: Vec::new(),
            metrics: ProtectedBlobIoMetrics::default(),
            namespace_object_count,
            namespace_stored_bytes,
            poisoned: false,
            #[cfg(test)]
            fault: None,
            #[cfg(test)]
            unsecured_test: false,
        };
        value.verify_root()?;
        Ok(value)
    }

    pub(crate) fn descriptor(&self) -> &ProtectedBlobNamespaceDescriptor {
        &self.descriptor
    }

    pub(crate) fn verify_namespace(&self) -> Result<(), ProtectedBlobError> {
        self.ensure_reusable()?;
        self.verify_root()
    }

    pub(crate) const fn namespace_usage(&self) -> (u64, u64) {
        (self.namespace_object_count, self.namespace_stored_bytes)
    }

    #[cfg(test)]
    pub(crate) const fn metrics(&self) -> ProtectedBlobIoMetrics {
        self.metrics
    }

    #[cfg(test)]
    pub(crate) fn cleanup_receipts(&self) -> &[ProtectedBlobCleanupReceipt] {
        &self.cleanup_receipts
    }

    #[cfg(test)]
    pub(crate) fn set_test_fault(&mut self, fault: ProtectedBlobTestFault) {
        self.fault = Some(fault);
    }

    pub(crate) fn materialize(
        &mut self,
        context: ProtectedBlobBindingContext,
        content: &[u8],
    ) -> Result<ReopenedProtectedBlob, ProtectedBlobError> {
        self.ensure_reusable()?;
        self.verify_root()?;
        validate_content(context.kind, content)?;
        let content_digest: BlobDigest = Sha256::digest(content).into();
        let address_digest = blob_address_digest(&self.descriptor, &context, &content_digest);
        let relative_name = relative_blob_name(&context, &content_digest, &address_digest);
        let expected_object_length = (PROTECTED_BLOB_HEADER_SIZE as u64)
            .checked_add(content.len() as u64)
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_namespace_limit_exceeded"))?;
        let next_object_count = self
            .namespace_object_count
            .checked_add(1)
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_namespace_limit_exceeded"))?;
        let next_stored_bytes = self
            .namespace_stored_bytes
            .checked_add(expected_object_length)
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_namespace_limit_exceeded"))?;
        if next_object_count > MAX_PROTECTED_BLOB_NAMESPACE_OBJECT_COUNT
            || next_stored_bytes > MAX_PROTECTED_BLOB_NAMESPACE_STORED_BYTES
        {
            return Err(ProtectedBlobError::new(
                "protected_blob_namespace_limit_exceeded",
            ));
        }
        let created = match create_relative_file(&self.root, &relative_name, {
            #[cfg(test)]
            {
                !self.unsecured_test
            }
            #[cfg(not(test))]
            {
                true
            }
        }) {
            Ok(created) => created,
            Err(error) => {
                self.refresh_namespace_usage_or_poison()?;
                return Err(error);
            }
        };
        let mut file = created
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_create_new_collision"))?;
        self.metrics.create_count += 1;
        #[cfg(test)]
        if self.fault == Some(ProtectedBlobTestFault::AfterCreateBeforeValidation) {
            self.fault = None;
            drop(file);
            self.refresh_namespace_usage_or_poison()?;
            return Err(ProtectedBlobError::new(
                "protected_blob_test_post_create_validation_fault",
            ));
        }
        let completed = (|| {
            let initial_identity = stable_blob_identity(&file, false)?;
            let initial_security = security_digest(&file)?;
            if initial_security != self.descriptor.file_security_digest {
                return Err(ProtectedBlobError::new("protected_blob_security_mismatch"));
            }
            let header = BlobHeader {
                context,
                generation_digest: self.descriptor.generation_digest,
                ledger_identity_digest: self.descriptor.ledger_identity_digest,
                namespace_source_digest: self.descriptor.source_digest,
                content_length: content.len() as u64,
                content_digest,
            };
            let header_bytes = encode_header(&header);
            self.write_complete_blob(&mut file, &header_bytes, content)?;
            #[cfg(test)]
            if self.fault == Some(ProtectedBlobTestFault::BeforeDirectoryFlush) {
                self.fault = None;
                return Err(ProtectedBlobError::new(
                    "protected_blob_test_directory_flush_fault",
                ));
            }
            flush_directory(&self.root)?;
            self.metrics.directory_flush_count += 1;
            #[cfg(test)]
            if self.fault == Some(ProtectedBlobTestFault::AfterDirectoryFlush) {
                self.fault = None;
                return Err(ProtectedBlobError::new(
                    "protected_blob_test_directory_flush_fault",
                ));
            }
            let (object_length, object_digest) = hash_file(&mut file, &mut self.metrics)?;
            let final_identity = stable_blob_identity(&file, false)?;
            if !same_blob_object(&initial_identity, &final_identity)
                || final_identity.byte_length != object_length
                || object_length != expected_object_length
                || security_digest(&file)? != initial_security
            {
                return Err(ProtectedBlobError::new(
                    "protected_blob_postwrite_identity_mismatch",
                ));
            }
            let object_identity_digest = stable_blob_identity_digest(b"blob", &final_identity);
            let binding_digest = blob_binding_digest(
                &self.descriptor,
                &header,
                object_length,
                object_digest,
                object_identity_digest,
                initial_security,
                address_digest,
            );
            let reference = ProtectedBlobReference {
                context,
                namespace_source_digest: self.descriptor.source_digest,
                content_length: content.len() as u64,
                content_digest,
                object_length,
                object_digest,
                object_identity_digest,
                object_security_digest: initial_security,
                address_digest,
                binding_digest,
                relative_name: relative_name.clone(),
            };
            let reopened_blob = read_and_validate(
                &mut file,
                &self.descriptor,
                Some(&reference),
                &mut self.metrics,
                ProtectedBlobReadLimits {
                    content_bytes: context.kind.maximum_content_size() as u64,
                    object_bytes: (context.kind.maximum_content_size() + PROTECTED_BLOB_HEADER_SIZE)
                        as u64,
                },
            )?;
            Ok((reopened_blob, reference, binding_digest))
        })();
        let (reopened_blob, reference, binding_digest) = match completed {
            Ok(value) => value,
            Err(error) => {
                drop(file);
                self.refresh_namespace_usage_or_poison()?;
                return Err(error);
            }
        };
        self.namespace_object_count = next_object_count;
        self.namespace_stored_bytes = next_stored_bytes;
        self.held.insert(
            binding_digest,
            HeldBlob {
                file,
                reference: reference.clone(),
            },
        );
        Ok(reopened_blob)
    }

    #[cfg(test)]
    pub(crate) fn reopen_bound(
        &mut self,
        context: ProtectedBlobBindingContext,
        content_digest: BlobDigest,
        binding_digest: BlobDigest,
    ) -> Result<ReopenedProtectedBlob, ProtectedBlobError> {
        self.reopen_bound_with_limits(
            context,
            content_digest,
            binding_digest,
            context.kind.maximum_content_size() as u64,
            (context.kind.maximum_content_size() + PROTECTED_BLOB_HEADER_SIZE) as u64,
        )
    }

    pub(crate) fn reopen_bound_with_limits(
        &mut self,
        context: ProtectedBlobBindingContext,
        content_digest: BlobDigest,
        binding_digest: BlobDigest,
        maximum_content_bytes: u64,
        maximum_object_bytes: u64,
    ) -> Result<ReopenedProtectedBlob, ProtectedBlobError> {
        self.ensure_reusable()?;
        self.verify_root()?;
        if content_digest == ZERO_DIGEST
            || binding_digest == ZERO_DIGEST
            || maximum_content_bytes == 0
            || maximum_object_bytes < PROTECTED_BLOB_HEADER_SIZE as u64
        {
            return Err(ProtectedBlobError::new("protected_blob_binding_invalid"));
        }
        let limits = ProtectedBlobReadLimits {
            content_bytes: maximum_content_bytes,
            object_bytes: maximum_object_bytes,
        };
        let address_digest = blob_address_digest(&self.descriptor, &context, &content_digest);
        let relative_name = relative_blob_name(&context, &content_digest, &address_digest);
        if let Some(held) = self.held.get_mut(&binding_digest) {
            if held.reference.context != context
                || held.reference.content_digest != content_digest
                || held.reference.relative_name != relative_name
            {
                return Err(ProtectedBlobError::new("protected_blob_binding_mismatch"));
            }
            return read_and_validate(
                &mut held.file,
                &self.descriptor,
                Some(&held.reference),
                &mut self.metrics,
                limits,
            );
        }
        let mut file = open_relative_file(&self.root, &relative_name, false)?
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_missing"))?;
        self.metrics.open_count += 1;
        let reopened =
            read_and_validate(&mut file, &self.descriptor, None, &mut self.metrics, limits)?;
        if reopened.reference.context != context
            || reopened.reference.content_digest != content_digest
            || reopened.reference.binding_digest != binding_digest
            || reopened.reference.relative_name != relative_name
        {
            return Err(ProtectedBlobError::new("protected_blob_binding_mismatch"));
        }
        self.held.insert(
            binding_digest,
            HeldBlob {
                file,
                reference: reopened.reference.clone(),
            },
        );
        Ok(reopened)
    }

    #[cfg(test)]
    pub(crate) fn verify_reference(
        &mut self,
        reference: &ProtectedBlobReference,
    ) -> Result<(), ProtectedBlobError> {
        let reopened = self.reopen_bound(
            reference.context,
            reference.content_digest,
            reference.binding_digest,
        )?;
        if reopened.reference != *reference {
            return Err(ProtectedBlobError::new("protected_blob_reference_changed"));
        }
        Ok(())
    }

    pub(crate) fn reconcile_unreferenced(
        &mut self,
        referenced_names: &BTreeSet<String>,
    ) -> Result<Vec<ProtectedBlobCleanupReceipt>, ProtectedBlobError> {
        self.ensure_reusable()?;
        self.verify_root()?;
        let mut deleted_count = 0usize;
        let mut namespace_may_have_mutated = false;
        let cleanup = (|| {
            let mut names = enumerate_relative_names(&self.root)?;
            names.sort();
            let mut receipts = Vec::new();
            for name in names {
                if referenced_names.contains(&name) {
                    continue;
                }
                let parsed = parse_relative_blob_name(&name)?;
                let expected_address =
                    blob_address_digest(&self.descriptor, &parsed.context, &parsed.content_digest);
                if parsed.address_digest != expected_address {
                    return Err(ProtectedBlobError::new(
                        "protected_blob_unreferenced_address_invalid",
                    ));
                }
                let mut file = open_relative_file(&self.root, &name, true)?
                    .ok_or_else(|| ProtectedBlobError::new("protected_blob_cleanup_race"))?;
                self.metrics.open_count += 1;
                let identity = stable_blob_identity(&file, false)?;
                let security = security_digest(&file)?;
                let maximum = parsed
                    .context
                    .kind
                    .maximum_content_size()
                    .checked_add(PROTECTED_BLOB_HEADER_SIZE)
                    .ok_or_else(|| ProtectedBlobError::new("protected_blob_size_invalid"))?
                    as u64;
                if identity.byte_length > maximum
                    || security != self.descriptor.file_security_digest
                    || identity.directory
                {
                    return Err(ProtectedBlobError::new(
                        "protected_blob_unreferenced_identity_invalid",
                    ));
                }
                let object_identity_digest = stable_blob_identity_digest(b"blob", &identity);
                let (observed_length, observed_digest) = hash_file(&mut file, &mut self.metrics)?;
                let receipt_digest = cleanup_receipt_digest(
                    &self.descriptor,
                    &name,
                    parsed.address_digest,
                    object_identity_digest,
                    security,
                    observed_length,
                    observed_digest,
                );
                namespace_may_have_mutated = true;
                delete_relative_file(&self.root, &name, file)?;
                #[cfg(test)]
                if self.fault == Some(ProtectedBlobTestFault::AfterCleanupDisposition) {
                    self.fault = None;
                    return Err(ProtectedBlobError::new(
                        "protected_blob_test_cleanup_disposition_fault",
                    ));
                }
                deleted_count += 1;
                if open_relative_file(&self.root, &name, false)?.is_some() {
                    return Err(ProtectedBlobError::new(
                        "protected_blob_cleanup_readback_failed",
                    ));
                }
                #[cfg(test)]
                if self.fault.is_some_and(
                    |fault| matches!(fault, ProtectedBlobTestFault::AfterCleanupDeletes(limit) if deleted_count >= limit),
                ) {
                    self.fault = None;
                    return Err(ProtectedBlobError::new(
                        "protected_blob_test_cleanup_mid_delete_fault",
                    ));
                }
                receipts.push(ProtectedBlobCleanupReceipt {
                    relative_name: name,
                    address_digest: parsed.address_digest,
                    object_identity_digest,
                    object_security_digest: security,
                    observed_length,
                    observed_digest,
                    receipt_digest,
                });
            }
            if !receipts.is_empty() {
                #[cfg(test)]
                if self.fault == Some(ProtectedBlobTestFault::BeforeCleanupDirectoryFlush) {
                    self.fault = None;
                    return Err(ProtectedBlobError::new(
                        "protected_blob_test_cleanup_directory_flush_fault",
                    ));
                }
                flush_directory(&self.root)?;
                self.metrics.directory_flush_count += 1;
                #[cfg(test)]
                if self.fault == Some(ProtectedBlobTestFault::AfterCleanupDirectoryFlush) {
                    self.fault = None;
                    return Err(ProtectedBlobError::new(
                        "protected_blob_test_cleanup_directory_flush_fault",
                    ));
                }
            }
            Ok(receipts)
        })();
        let receipts = match cleanup {
            Ok(receipts) => receipts,
            Err(error) => {
                if namespace_may_have_mutated {
                    self.refresh_namespace_usage_or_poison()?;
                }
                return Err(error);
            }
        };
        if !receipts.is_empty() {
            self.refresh_namespace_usage_or_poison()?;
            let next_cleanup_count = self
                .metrics
                .cleanup_count
                .checked_add(receipts.len() as u64)
                .ok_or_else(|| ProtectedBlobError::new("protected_blob_metric_overflow"))?;
            self.metrics.cleanup_count = next_cleanup_count;
            self.cleanup_receipts.extend(receipts.iter().cloned());
        }
        Ok(receipts)
    }

    fn ensure_reusable(&self) -> Result<(), ProtectedBlobError> {
        if self.poisoned {
            Err(ProtectedBlobError::new("protected_blob_authority_poisoned"))
        } else {
            Ok(())
        }
    }

    fn refresh_namespace_usage_or_poison(&mut self) -> Result<(), ProtectedBlobError> {
        if self.refresh_namespace_usage().is_err() {
            self.poisoned = true;
            return Err(ProtectedBlobError::new("protected_blob_authority_poisoned"));
        }
        Ok(())
    }

    fn refresh_namespace_usage(&mut self) -> Result<(), ProtectedBlobError> {
        let (object_count, stored_bytes) = scan_namespace_usage(&self.root, &self.descriptor)
            .map_err(|_| ProtectedBlobError::new("protected_blob_namespace_accounting_invalid"))?;
        self.namespace_object_count = object_count;
        self.namespace_stored_bytes = stored_bytes;
        Ok(())
    }

    fn write_complete_blob(
        &mut self,
        file: &mut File,
        header: &[u8; PROTECTED_BLOB_HEADER_SIZE],
        content: &[u8],
    ) -> Result<(), ProtectedBlobError> {
        #[cfg(test)]
        if self.fault == Some(ProtectedBlobTestFault::BeforeFirstWrite) {
            self.fault = None;
            return Err(ProtectedBlobError::new("protected_blob_test_write_fault"));
        }
        #[cfg(test)]
        let total_length = header.len() + content.len();
        #[cfg(test)]
        let mut written = 0usize;
        for source in [header.as_slice(), content] {
            for chunk in source.chunks(PROTECTED_BLOB_IO_CHUNK_SIZE) {
                file.write_all(chunk)
                    .map_err(|_| ProtectedBlobError::new("protected_blob_write_failed"))?;
                self.metrics.write_call_count += 1;
                self.metrics.bytes_written += chunk.len() as u64;
                #[cfg(test)]
                {
                    written += chunk.len();
                    if self
                        .fault
                        .is_some_and(|fault| matches!(fault, ProtectedBlobTestFault::AfterBytes(limit) if written >= limit && written < total_length))
                    {
                        self.fault = None;
                        return Err(ProtectedBlobError::new("protected_blob_test_write_fault"));
                    }
                }
            }
        }
        #[cfg(test)]
        if self.fault.is_some_and(
            |fault| matches!(fault, ProtectedBlobTestFault::AfterBytes(limit) if written >= limit),
        ) {
            self.fault = None;
            return Err(ProtectedBlobError::new("protected_blob_test_write_fault"));
        }
        #[cfg(test)]
        if self.fault == Some(ProtectedBlobTestFault::BeforeFlush) {
            self.fault = None;
            return Err(ProtectedBlobError::new("protected_blob_test_flush_fault"));
        }
        file.sync_all()
            .map_err(|_| ProtectedBlobError::new("protected_blob_flush_failed"))?;
        self.metrics.blob_flush_count += 1;
        #[cfg(test)]
        if self.fault == Some(ProtectedBlobTestFault::AfterFlush) {
            self.fault = None;
            return Err(ProtectedBlobError::new("protected_blob_test_flush_fault"));
        }
        Ok(())
    }

    fn verify_root(&self) -> Result<(), ProtectedBlobError> {
        let identity = stable_blob_identity(&self.root, true)?;
        if stable_blob_identity_digest(b"root", &identity) != self.descriptor.root_identity_digest
            || security_digest(&self.root)? != self.descriptor.root_security_digest
        {
            return Err(ProtectedBlobError::new(
                "protected_blob_root_identity_changed",
            ));
        }
        Ok(())
    }
}

#[derive(Debug)]
struct ParsedBlobName {
    context: ProtectedBlobBindingContext,
    content_digest: BlobDigest,
    address_digest: BlobDigest,
}

fn scan_namespace_usage(
    root: &File,
    descriptor: &ProtectedBlobNamespaceDescriptor,
) -> Result<(u64, u64), ProtectedBlobError> {
    scan_namespace_usage_with_limits(
        root,
        descriptor,
        MAX_PROTECTED_BLOB_NAMESPACE_OBJECT_COUNT,
        MAX_PROTECTED_BLOB_NAMESPACE_STORED_BYTES,
    )
}

fn scan_namespace_usage_with_limits(
    root: &File,
    descriptor: &ProtectedBlobNamespaceDescriptor,
    maximum_objects: u64,
    maximum_stored_bytes: u64,
) -> Result<(u64, u64), ProtectedBlobError> {
    let maximum_names = usize::try_from(maximum_objects)
        .map_err(|_| ProtectedBlobError::new("protected_blob_namespace_limit_exceeded"))?;
    let names = enumerate_relative_names_with_limit(root, maximum_names)?;
    let mut object_count = 0u64;
    let mut stored_bytes = 0u64;
    for name in names {
        let parsed = parse_relative_blob_name(&name)?;
        let expected_address =
            blob_address_digest(descriptor, &parsed.context, &parsed.content_digest);
        if parsed.address_digest != expected_address {
            return Err(ProtectedBlobError::new(
                "protected_blob_unreferenced_address_invalid",
            ));
        }
        let file = open_relative_file(root, &name, false)?
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_scan_race"))?;
        let identity = stable_blob_identity(&file, false)?;
        let maximum = parsed
            .context
            .kind
            .maximum_content_size()
            .checked_add(PROTECTED_BLOB_HEADER_SIZE)
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_size_invalid"))?
            as u64;
        if identity.directory || identity.byte_length > maximum {
            return Err(ProtectedBlobError::new(
                "protected_blob_namespace_identity_invalid",
            ));
        }
        if security_digest(&file)? != descriptor.file_security_digest {
            return Err(ProtectedBlobError::new("protected_blob_security_mismatch"));
        }
        object_count = object_count
            .checked_add(1)
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_namespace_limit_exceeded"))?;
        stored_bytes = stored_bytes
            .checked_add(identity.byte_length)
            .ok_or_else(|| ProtectedBlobError::new("protected_blob_namespace_limit_exceeded"))?;
        if object_count > maximum_objects || stored_bytes > maximum_stored_bytes {
            return Err(ProtectedBlobError::new(
                "protected_blob_namespace_limit_exceeded",
            ));
        }
    }
    Ok((object_count, stored_bytes))
}

fn validate_root_path(path: &Path) -> Result<(), ProtectedBlobError> {
    if !path.is_absolute()
        || path.as_os_str().is_empty()
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(ProtectedBlobError::new("protected_blob_root_invalid"));
    }
    Ok(())
}

fn validate_content(kind: ProtectedBlobKind, content: &[u8]) -> Result<(), ProtectedBlobError> {
    if content.is_empty() || content.len() > kind.maximum_content_size() {
        return Err(ProtectedBlobError::new("protected_blob_content_invalid"));
    }
    Ok(())
}

fn encode_header(header: &BlobHeader) -> [u8; PROTECTED_BLOB_HEADER_SIZE] {
    let mut bytes = [0u8; PROTECTED_BLOB_HEADER_SIZE];
    bytes[..8].copy_from_slice(BLOB_MAGIC);
    bytes[8..10].copy_from_slice(&PROTECTED_BLOB_SCHEMA_VERSION.to_be_bytes());
    bytes[10] = header.context.kind as u8;
    let mut offset = 16usize;
    for digest in [
        header.generation_digest,
        header.ledger_identity_digest,
        header.namespace_source_digest,
        header.context.ticket_digest,
        header.context.run_binding_digest,
        header.context.prepared_source_digest,
        header.context.policy_snapshot_digest,
        header.context.recovery_bundle_digest,
        header.content_digest,
    ] {
        bytes[offset..offset + 32].copy_from_slice(&digest);
        offset += 32;
    }
    bytes[offset..offset + 8].copy_from_slice(&header.content_length.to_be_bytes());
    offset += 8;
    debug_assert_eq!(offset + 32, PROTECTED_BLOB_HEADER_SIZE);
    let mut digest = Sha256::new();
    digest.update(BLOB_HEADER_DOMAIN);
    digest.update(&bytes[..offset]);
    bytes[offset..].copy_from_slice(&digest.finalize());
    bytes
}

fn decode_header(bytes: &[u8]) -> Result<BlobHeader, ProtectedBlobError> {
    if bytes.len() != PROTECTED_BLOB_HEADER_SIZE
        || &bytes[..8] != BLOB_MAGIC
        || u16::from_be_bytes(bytes[8..10].try_into().unwrap()) != PROTECTED_BLOB_SCHEMA_VERSION
        || bytes[11..16].iter().any(|byte| *byte != 0)
    {
        return Err(ProtectedBlobError::new("protected_blob_header_invalid"));
    }
    let mut offset = 16usize;
    let mut take_digest = || {
        let digest: BlobDigest = bytes[offset..offset + 32].try_into().unwrap();
        offset += 32;
        digest
    };
    let generation_digest = take_digest();
    let ledger_identity_digest = take_digest();
    let namespace_source_digest = take_digest();
    let ticket_digest = take_digest();
    let run_binding_digest = take_digest();
    let prepared_source_digest = take_digest();
    let policy_snapshot_digest = take_digest();
    let recovery_bundle_digest = take_digest();
    let content_digest = take_digest();
    let content_length = u64::from_be_bytes(bytes[offset..offset + 8].try_into().unwrap());
    offset += 8;
    let mut digest = Sha256::new();
    digest.update(BLOB_HEADER_DOMAIN);
    digest.update(&bytes[..offset]);
    if digest.finalize()[..] != bytes[offset..]
        || [
            generation_digest,
            ledger_identity_digest,
            namespace_source_digest,
            ticket_digest,
            run_binding_digest,
            prepared_source_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
            content_digest,
        ]
        .iter()
        .any(is_zero)
    {
        return Err(ProtectedBlobError::new("protected_blob_header_invalid"));
    }
    Ok(BlobHeader {
        context: ProtectedBlobBindingContext::new(
            ProtectedBlobKind::from_u8(bytes[10])?,
            ticket_digest,
            run_binding_digest,
            prepared_source_digest,
            policy_snapshot_digest,
            recovery_bundle_digest,
        )?,
        generation_digest,
        ledger_identity_digest,
        namespace_source_digest,
        content_length,
        content_digest,
    })
}

fn read_and_validate(
    file: &mut File,
    descriptor: &ProtectedBlobNamespaceDescriptor,
    expected: Option<&ProtectedBlobReference>,
    metrics: &mut ProtectedBlobIoMetrics,
    limits: ProtectedBlobReadLimits,
) -> Result<ReopenedProtectedBlob, ProtectedBlobError> {
    let identity = stable_blob_identity(file, false)?;
    let security = security_digest(file)?;
    if security != descriptor.file_security_digest {
        return Err(ProtectedBlobError::new("protected_blob_security_mismatch"));
    }
    let object_identity_digest = stable_blob_identity_digest(b"blob", &identity);
    file.seek(SeekFrom::Start(0))
        .map_err(|_| ProtectedBlobError::new("protected_blob_read_failed"))?;
    let mut header_bytes = [0u8; PROTECTED_BLOB_HEADER_SIZE];
    file.read_exact(&mut header_bytes)
        .map_err(|_| ProtectedBlobError::new("protected_blob_read_failed"))?;
    metrics.read_call_count += 1;
    metrics.bytes_read += PROTECTED_BLOB_HEADER_SIZE as u64;
    let header = decode_header(&header_bytes)?;
    let content_length = usize::try_from(header.content_length)
        .map_err(|_| ProtectedBlobError::new("protected_blob_size_invalid"))?;
    if header.generation_digest != descriptor.generation_digest
        || header.ledger_identity_digest != descriptor.ledger_identity_digest
        || header.namespace_source_digest != descriptor.source_digest
        || header.content_length == 0
        || content_length > header.context.kind.maximum_content_size()
        || header.content_length > limits.content_bytes
        || identity.byte_length > limits.object_bytes
        || identity.byte_length
            != (PROTECTED_BLOB_HEADER_SIZE as u64)
                .checked_add(header.content_length)
                .ok_or_else(|| ProtectedBlobError::new("protected_blob_size_invalid"))?
    {
        return Err(ProtectedBlobError::new(
            if header.content_length > limits.content_bytes
                || identity.byte_length > limits.object_bytes
            {
                "protected_blob_generation_limit_exceeded"
            } else {
                "protected_blob_header_mismatch"
            },
        ));
    }
    let mut content = Vec::new();
    content
        .try_reserve_exact(content_length)
        .map_err(|_| ProtectedBlobError::new("protected_blob_allocation_failed"))?;
    let mut remaining = content_length;
    let mut buffer = [0u8; PROTECTED_BLOB_IO_CHUNK_SIZE];
    let mut content_hasher = Sha256::new();
    while remaining > 0 {
        let take = remaining.min(buffer.len());
        file.read_exact(&mut buffer[..take])
            .map_err(|_| ProtectedBlobError::new("protected_blob_read_failed"))?;
        metrics.read_call_count += 1;
        metrics.bytes_read += take as u64;
        content_hasher.update(&buffer[..take]);
        content.extend_from_slice(&buffer[..take]);
        remaining -= take;
    }
    let content_digest: BlobDigest = content_hasher.finalize().into();
    if content_digest != header.content_digest {
        return Err(ProtectedBlobError::new(
            "protected_blob_content_digest_mismatch",
        ));
    }
    let address_digest = blob_address_digest(descriptor, &header.context, &content_digest);
    let relative_name = relative_blob_name(&header.context, &content_digest, &address_digest);
    let (object_length, object_digest) = hash_file(file, metrics)?;
    let binding_digest = blob_binding_digest(
        descriptor,
        &header,
        object_length,
        object_digest,
        object_identity_digest,
        security,
        address_digest,
    );
    let reference = ProtectedBlobReference {
        context: header.context,
        namespace_source_digest: descriptor.source_digest,
        content_length: header.content_length,
        content_digest,
        object_length,
        object_digest,
        object_identity_digest,
        object_security_digest: security,
        address_digest,
        binding_digest,
        relative_name,
    };
    if expected.is_some_and(|expected| expected != &reference) {
        return Err(ProtectedBlobError::new("protected_blob_reference_changed"));
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| ProtectedBlobError::new("protected_blob_read_failed"))?;
    Ok(ReopenedProtectedBlob { reference, content })
}

fn blob_address_digest(
    descriptor: &ProtectedBlobNamespaceDescriptor,
    context: &ProtectedBlobBindingContext,
    content_digest: &BlobDigest,
) -> BlobDigest {
    let mut digest = Sha256::new();
    digest.update(BLOB_ADDRESS_DOMAIN);
    digest.update(PROTECTED_BLOB_SCHEMA_VERSION.to_be_bytes());
    digest.update(descriptor.generation_digest);
    digest.update(descriptor.ledger_identity_digest);
    digest.update(descriptor.source_digest);
    digest.update([context.kind as u8]);
    digest.update(context.ticket_digest);
    digest.update(context.run_binding_digest);
    digest.update(content_digest);
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
fn blob_binding_digest(
    descriptor: &ProtectedBlobNamespaceDescriptor,
    header: &BlobHeader,
    object_length: u64,
    object_digest: BlobDigest,
    object_identity_digest: BlobDigest,
    object_security_digest: BlobDigest,
    address_digest: BlobDigest,
) -> BlobDigest {
    let mut digest = Sha256::new();
    digest.update(BLOB_BINDING_DOMAIN);
    digest.update(PROTECTED_BLOB_SCHEMA_VERSION.to_be_bytes());
    digest.update(descriptor.generation_digest);
    digest.update(descriptor.ledger_identity_digest);
    digest.update(descriptor.source_digest);
    digest.update([header.context.kind as u8]);
    digest.update(header.context.ticket_digest);
    digest.update(header.context.run_binding_digest);
    digest.update(header.context.prepared_source_digest);
    digest.update(header.context.policy_snapshot_digest);
    digest.update(header.context.recovery_bundle_digest);
    digest.update(object_identity_digest);
    digest.update(object_security_digest);
    digest.update(object_length.to_be_bytes());
    digest.update(object_digest);
    digest.update(header.content_length.to_be_bytes());
    digest.update(header.content_digest);
    digest.update(address_digest);
    digest.finalize().into()
}

#[allow(clippy::too_many_arguments)]
fn cleanup_receipt_digest(
    descriptor: &ProtectedBlobNamespaceDescriptor,
    relative_name: &str,
    address_digest: BlobDigest,
    object_identity_digest: BlobDigest,
    object_security_digest: BlobDigest,
    observed_length: u64,
    observed_digest: BlobDigest,
) -> BlobDigest {
    let mut digest = Sha256::new();
    digest.update(BLOB_CLEANUP_DOMAIN);
    digest.update(PROTECTED_BLOB_SCHEMA_VERSION.to_be_bytes());
    digest.update(descriptor.generation_digest);
    digest.update(descriptor.ledger_identity_digest);
    digest.update(descriptor.source_digest);
    digest.update((relative_name.len() as u64).to_be_bytes());
    digest.update(relative_name.as_bytes());
    digest.update(address_digest);
    digest.update(object_identity_digest);
    digest.update(object_security_digest);
    digest.update(observed_length.to_be_bytes());
    digest.update(observed_digest);
    digest.finalize().into()
}

fn relative_blob_name(
    context: &ProtectedBlobBindingContext,
    content_digest: &BlobDigest,
    address_digest: &BlobDigest,
) -> String {
    format!(
        "v1-k{:02x}-t{}-r{}-c{}-a{}.vpb",
        context.kind as u8,
        base32_encode(&context.ticket_digest),
        base32_encode(&context.run_binding_digest),
        base32_encode(content_digest),
        base32_encode(address_digest),
    )
}

fn parse_relative_blob_name(name: &str) -> Result<ParsedBlobName, ProtectedBlobError> {
    if name.len() > 240 || name.contains(['/', '\\', '\0']) {
        return Err(ProtectedBlobError::new("protected_blob_namespace_unknown"));
    }
    let parts = name.split('-').collect::<Vec<_>>();
    if parts.len() != 6
        || parts[0] != "v1"
        || parts[1].len() != 3
        || !parts[1].starts_with('k')
        || !parts[2].starts_with('t')
        || !parts[3].starts_with('r')
        || !parts[4].starts_with('c')
        || !parts[5].starts_with('a')
        || !parts[5].ends_with(".vpb")
    {
        return Err(ProtectedBlobError::new("protected_blob_namespace_unknown"));
    }
    let kind_value = u8::from_str_radix(&parts[1][1..], 16)
        .map_err(|_| ProtectedBlobError::new("protected_blob_namespace_unknown"))?;
    let kind = ProtectedBlobKind::from_u8(kind_value)?;
    if parts[1] != format!("k{:02x}", kind as u8) {
        return Err(ProtectedBlobError::new("protected_blob_namespace_unknown"));
    }
    let ticket_digest = base32_decode_digest(&parts[2][1..])?;
    let run_binding_digest = base32_decode_digest(&parts[3][1..])?;
    let content_digest = base32_decode_digest(&parts[4][1..])?;
    let address_text = &parts[5][1..parts[5].len() - 4];
    let address_digest = base32_decode_digest(address_text)?;
    // An unreferenced name intentionally cannot claim prepared/policy/recovery
    // facts. Those are verified only after a committed bind reopens its header.
    let placeholder = [1u8; 32];
    let context = ProtectedBlobBindingContext::new(
        kind,
        ticket_digest,
        run_binding_digest,
        placeholder,
        placeholder,
        placeholder,
    )?;
    Ok(ParsedBlobName {
        context,
        content_digest,
        address_digest,
    })
}

fn base32_encode(value: &BlobDigest) -> String {
    const ALPHABET: &[u8; 32] = b"abcdefghijklmnopqrstuvwxyz234567";
    let mut output = String::with_capacity(52);
    let mut accumulator = 0u64;
    let mut bits = 0u8;
    for byte in value {
        accumulator = (accumulator << 8) | u64::from(*byte);
        bits += 8;
        while bits >= 5 {
            bits -= 5;
            let index = ((accumulator >> bits) & 0x1f) as usize;
            output.push(ALPHABET[index] as char);
        }
    }
    if bits > 0 {
        let index = ((accumulator << (5 - bits)) & 0x1f) as usize;
        output.push(ALPHABET[index] as char);
    }
    output
}

fn base32_decode_digest(value: &str) -> Result<BlobDigest, ProtectedBlobError> {
    if value.len() != 52 {
        return Err(ProtectedBlobError::new("protected_blob_namespace_unknown"));
    }
    let mut output = Vec::with_capacity(32);
    let mut accumulator = 0u64;
    let mut bits = 0u8;
    for byte in value.bytes() {
        let digit = match byte {
            b'a'..=b'z' => byte - b'a',
            b'2'..=b'7' => byte - b'2' + 26,
            _ => return Err(ProtectedBlobError::new("protected_blob_namespace_unknown")),
        };
        accumulator = (accumulator << 5) | u64::from(digit);
        bits += 5;
        if bits >= 8 {
            bits -= 8;
            output.push(((accumulator >> bits) & 0xff) as u8);
        }
    }
    if output.len() != 32 || accumulator & ((1u64 << bits) - 1) != 0 {
        return Err(ProtectedBlobError::new("protected_blob_namespace_unknown"));
    }
    Ok(output.try_into().unwrap())
}

fn hash_file(
    file: &mut File,
    metrics: &mut ProtectedBlobIoMetrics,
) -> Result<(u64, BlobDigest), ProtectedBlobError> {
    let length = file
        .metadata()
        .map_err(|_| ProtectedBlobError::new("protected_blob_metadata_failed"))?
        .len();
    file.seek(SeekFrom::Start(0))
        .map_err(|_| ProtectedBlobError::new("protected_blob_read_failed"))?;
    let mut remaining = length;
    let mut buffer = [0u8; PROTECTED_BLOB_IO_CHUNK_SIZE];
    let mut digest = Sha256::new();
    while remaining > 0 {
        let take = remaining.min(buffer.len() as u64) as usize;
        file.read_exact(&mut buffer[..take])
            .map_err(|_| ProtectedBlobError::new("protected_blob_read_failed"))?;
        metrics.read_call_count += 1;
        metrics.bytes_read += take as u64;
        digest.update(&buffer[..take]);
        remaining -= take as u64;
    }
    Ok((length, digest.finalize().into()))
}

fn stable_blob_identity_digest(role: &[u8], identity: &StableBlobIdentity) -> BlobDigest {
    let mut digest = Sha256::new();
    digest.update(BLOB_OBJECT_IDENTITY_DOMAIN);
    digest.update((role.len() as u64).to_be_bytes());
    digest.update(role);
    #[cfg(unix)]
    {
        digest.update(identity.device.to_be_bytes());
        digest.update(identity.inode.to_be_bytes());
        digest.update(identity.link_count.to_be_bytes());
    }
    #[cfg(windows)]
    {
        digest.update(identity.volume_serial.to_be_bytes());
        digest.update(identity.file_id);
        digest.update(identity.link_count.to_be_bytes());
        digest.update(identity.attributes.to_be_bytes());
    }
    // Directory byte length is mutable namespace metadata and changes as
    // entries are created. Object identity for the held root must not drift
    // merely because this authority appended an immutable child.
    if !identity.directory {
        digest.update(identity.byte_length.to_be_bytes());
    }
    digest.update([u8::from(identity.directory)]);
    digest.finalize().into()
}

fn same_blob_object(left: &StableBlobIdentity, right: &StableBlobIdentity) -> bool {
    #[cfg(unix)]
    let same = left.device == right.device && left.inode == right.inode;
    #[cfg(windows)]
    let same = left.volume_serial == right.volume_serial && left.file_id == right.file_id;
    same && left.link_count == right.link_count && left.directory == right.directory && {
        #[cfg(windows)]
        {
            left.attributes == right.attributes
        }
        #[cfg(not(windows))]
        {
            true
        }
    }
}

#[cfg(unix)]
fn stable_blob_identity(
    file: &File,
    directory: bool,
) -> Result<StableBlobIdentity, ProtectedBlobError> {
    let metadata = file
        .metadata()
        .map_err(|_| ProtectedBlobError::new("protected_blob_identity_unavailable"))?;
    if metadata.is_dir() != directory
        || (!directory && (!metadata.is_file() || metadata.nlink() != 1))
    {
        return Err(ProtectedBlobError::new("protected_blob_identity_invalid"));
    }
    Ok(StableBlobIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
        link_count: metadata.nlink(),
        byte_length: metadata.len(),
        directory,
    })
}

#[cfg(windows)]
fn stable_blob_identity(
    file: &File,
    directory: bool,
) -> Result<StableBlobIdentity, ProtectedBlobError> {
    let metadata = file
        .metadata()
        .map_err(|_| ProtectedBlobError::new("protected_blob_identity_unavailable"))?;
    let mut id = unsafe { zeroed::<FILE_ID_INFO>() };
    let mut standard = unsafe { zeroed::<FILE_STANDARD_INFO>() };
    let raw = file.as_raw_handle().cast();
    if unsafe {
        GetFileInformationByHandleEx(
            raw,
            FileIdInfo,
            (&mut id as *mut FILE_ID_INFO).cast(),
            size_of::<FILE_ID_INFO>() as u32,
        )
    } == 0
        || unsafe {
            GetFileInformationByHandleEx(
                raw,
                FileStandardInfo,
                (&mut standard as *mut FILE_STANDARD_INFO).cast(),
                size_of::<FILE_STANDARD_INFO>() as u32,
            )
        } == 0
        || !windows_blob_identity_observation_valid(
            directory,
            metadata.len(),
            metadata.file_attributes(),
            id.VolumeSerialNumber,
            &id.FileId.Identifier,
            standard.NumberOfLinks,
            standard.EndOfFile,
            standard.Directory != 0,
            standard.DeletePending != 0,
        )
    {
        return Err(ProtectedBlobError::new("protected_blob_identity_invalid"));
    }
    Ok(StableBlobIdentity {
        volume_serial: id.VolumeSerialNumber,
        file_id: id.FileId.Identifier,
        link_count: standard.NumberOfLinks,
        attributes: metadata.file_attributes(),
        byte_length: metadata.len(),
        directory,
    })
}

#[cfg(windows)]
#[allow(clippy::too_many_arguments)]
fn windows_blob_identity_observation_valid(
    expected_directory: bool,
    metadata_length: u64,
    attributes: u32,
    volume_serial: u64,
    file_id: &[u8; 16],
    link_count: u32,
    end_of_file: i64,
    observed_directory: bool,
    delete_pending: bool,
) -> bool {
    observed_directory == expected_directory
        && !delete_pending
        && (expected_directory || link_count == 1)
        && link_count > 0
        && end_of_file >= 0
        && volume_serial != 0
        && file_id.iter().any(|byte| *byte != 0)
        && u64::try_from(end_of_file).ok() == Some(metadata_length)
        && attributes & FILE_ATTRIBUTE_REPARSE_POINT == 0
        && expected_directory == (attributes & FILE_ATTRIBUTE_DIRECTORY == FILE_ATTRIBUTE_DIRECTORY)
}

#[cfg(unix)]
fn security_digest(file: &File) -> Result<BlobDigest, ProtectedBlobError> {
    let metadata = file
        .metadata()
        .map_err(|_| ProtectedBlobError::new("protected_blob_security_readback_failed"))?;
    let mut digest = Sha256::new();
    digest.update(b"vrcforge-protected-blob-unix-security-v1\0");
    digest.update(metadata.uid().to_be_bytes());
    digest.update(metadata.gid().to_be_bytes());
    digest.update(metadata.mode().to_be_bytes());
    Ok(digest.finalize().into())
}

#[cfg(windows)]
fn security_digest(file: &File) -> Result<BlobDigest, ProtectedBlobError> {
    let information = OWNER_SECURITY_INFORMATION
        | GROUP_SECURITY_INFORMATION
        | DACL_SECURITY_INFORMATION
        | LABEL_SECURITY_INFORMATION;
    let mut descriptor: PSECURITY_DESCRIPTOR = ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            file.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            information,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 || descriptor.is_null() {
        return Err(ProtectedBlobError::new(
            "protected_blob_security_readback_failed",
        ));
    }
    let mut encoded = ptr::null_mut();
    let mut length = 0u32;
    let converted = unsafe {
        ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            SDDL_REVISION_1,
            information,
            &mut encoded,
            &mut length,
        )
    };
    unsafe {
        LocalFree(descriptor.cast());
    }
    if converted == 0 || encoded.is_null() || length == 0 || length > 16 * 1024 {
        if !encoded.is_null() {
            unsafe { LocalFree(encoded.cast()) };
        }
        return Err(ProtectedBlobError::new(
            "protected_blob_security_readback_failed",
        ));
    }
    let words = unsafe { slice::from_raw_parts(encoded, length as usize) };
    let canonical = String::from_utf16(words)
        .map_err(|_| ProtectedBlobError::new("protected_blob_security_readback_failed"));
    unsafe {
        LocalFree(encoded.cast());
    }
    Ok(Sha256::digest(canonical?.as_bytes()).into())
}

fn enumerate_relative_names(parent: &File) -> Result<Vec<String>, ProtectedBlobError> {
    enumerate_relative_names_with_limit(parent, MAX_PROTECTED_BLOB_NAMESPACE_OBJECT_COUNT as usize)
}

#[cfg(unix)]
fn enumerate_relative_names_with_limit(
    parent: &File,
    maximum_names: usize,
) -> Result<Vec<String>, ProtectedBlobError> {
    use std::os::fd::AsRawFd;
    let held_path = PathBuf::from(format!("/proc/self/fd/{}", parent.as_raw_fd()));
    let mut names = Vec::new();
    for entry in fs::read_dir(held_path)
        .map_err(|_| ProtectedBlobError::new("protected_blob_enumeration_failed"))?
    {
        let name = entry
            .map_err(|_| ProtectedBlobError::new("protected_blob_enumeration_failed"))?
            .file_name()
            .into_string()
            .map_err(|_| ProtectedBlobError::new("protected_blob_namespace_unknown"))?;
        if name != "." && name != ".." {
            if names.len() >= maximum_names {
                return Err(ProtectedBlobError::new(
                    "protected_blob_namespace_limit_exceeded",
                ));
            }
            names.push(name);
        }
    }
    Ok(names)
}

#[cfg(windows)]
fn enumerate_relative_names_with_limit(
    parent: &File,
    maximum_names: usize,
) -> Result<Vec<String>, ProtectedBlobError> {
    const STATUS_NO_MORE_FILES: i32 = 0x8000_0006u32 as i32;
    const BUFFER_SIZE: usize = 64 * 1024;
    const NAME_OFFSET: usize = 12;

    let mut restart = 1u8;
    let mut names = Vec::new();
    loop {
        let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
        let mut buffer = vec![0u8; BUFFER_SIZE];
        let status = unsafe {
            NtQueryDirectoryFile(
                parent.as_raw_handle().cast(),
                ptr::null_mut(),
                None,
                ptr::null(),
                &mut status_block,
                buffer.as_mut_ptr().cast(),
                BUFFER_SIZE as u32,
                FileNamesInformation,
                0,
                ptr::null(),
                restart,
            )
        };
        restart = 0;
        if status == STATUS_NO_MORE_FILES {
            break;
        }
        if status < 0 || status_block.Information == 0 || status_block.Information > BUFFER_SIZE {
            return Err(ProtectedBlobError::new("protected_blob_enumeration_failed"));
        }
        let used = status_block.Information;
        let mut offset = 0usize;
        loop {
            if offset.checked_add(NAME_OFFSET).is_none_or(|end| end > used) {
                return Err(ProtectedBlobError::new("protected_blob_enumeration_failed"));
            }
            let next = u32::from_ne_bytes(buffer[offset..offset + 4].try_into().unwrap()) as usize;
            let name_bytes =
                u32::from_ne_bytes(buffer[offset + 8..offset + 12].try_into().unwrap()) as usize;
            let name_end = offset
                .checked_add(NAME_OFFSET)
                .and_then(|start| start.checked_add(name_bytes))
                .ok_or_else(|| ProtectedBlobError::new("protected_blob_enumeration_failed"))?;
            if name_bytes == 0 || name_bytes > 480 || name_bytes % 2 != 0 || name_end > used {
                return Err(ProtectedBlobError::new("protected_blob_enumeration_failed"));
            }
            let words = buffer[offset + NAME_OFFSET..name_end]
                .chunks_exact(2)
                .map(|pair| u16::from_ne_bytes([pair[0], pair[1]]))
                .collect::<Vec<_>>();
            let name = String::from_utf16(&words)
                .map_err(|_| ProtectedBlobError::new("protected_blob_namespace_unknown"))?;
            if name != "." && name != ".." {
                if names.len() >= maximum_names {
                    return Err(ProtectedBlobError::new(
                        "protected_blob_namespace_limit_exceeded",
                    ));
                }
                names.push(name);
            }
            if next == 0 {
                break;
            }
            if next < NAME_OFFSET || next % 4 != 0 {
                return Err(ProtectedBlobError::new("protected_blob_enumeration_failed"));
            }
            offset = offset
                .checked_add(next)
                .ok_or_else(|| ProtectedBlobError::new("protected_blob_enumeration_failed"))?;
        }
    }
    Ok(names)
}

#[cfg(unix)]
fn flush_directory(parent: &File) -> Result<(), ProtectedBlobError> {
    parent
        .sync_all()
        .map_err(|_| ProtectedBlobError::new("protected_blob_directory_flush_failed"))
}

#[cfg(windows)]
fn flush_directory(parent: &File) -> Result<(), ProtectedBlobError> {
    let mut status = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let result = unsafe { NtFlushBuffersFile(parent.as_raw_handle().cast(), &mut status) };
    if result < 0 {
        return Err(ProtectedBlobError::new(
            "protected_blob_directory_flush_failed",
        ));
    }
    Ok(())
}

#[cfg(unix)]
fn open_root(path: &Path) -> Result<File, ProtectedBlobError> {
    File::open(path).map_err(|_| ProtectedBlobError::new("protected_blob_root_open_failed"))
}

#[cfg(all(windows, test))]
fn open_root(path: &Path) -> Result<File, ProtectedBlobError> {
    let encoded = path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let handle = unsafe {
        CreateFileW(
            encoded.as_ptr(),
            FILE_LIST_DIRECTORY
                | FILE_TRAVERSE
                | FILE_READ_ATTRIBUTES
                | FILE_ADD_FILE
                | READ_CONTROL
                | SYNCHRONIZE,
            0,
            ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return Err(ProtectedBlobError::new("protected_blob_root_open_failed"));
    }
    Ok(unsafe { File::from_raw_handle(handle as RawHandle) })
}

#[cfg(unix)]
fn create_relative_file(
    parent: &File,
    name: &str,
    _apply_production_security: bool,
) -> Result<Option<File>, ProtectedBlobError> {
    use std::os::fd::AsRawFd;
    validate_relative_name(name)?;
    let parent_path = PathBuf::from(format!("/proc/self/fd/{}", parent.as_raw_fd()));
    match fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(parent_path.join(name))
    {
        Ok(file) => Ok(Some(file)),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => Ok(None),
        Err(_) => Err(ProtectedBlobError::new("protected_blob_create_failed")),
    }
}

#[cfg(unix)]
fn open_relative_file(
    parent: &File,
    name: &str,
    _delete: bool,
) -> Result<Option<File>, ProtectedBlobError> {
    use std::os::fd::AsRawFd;
    validate_relative_name(name)?;
    let path = PathBuf::from(format!("/proc/self/fd/{}", parent.as_raw_fd())).join(name);
    match File::open(path) {
        Ok(file) => Ok(Some(file)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(ProtectedBlobError::new("protected_blob_open_failed")),
    }
}

#[cfg(unix)]
fn delete_relative_file(parent: &File, name: &str, file: File) -> Result<(), ProtectedBlobError> {
    use std::os::fd::AsRawFd;
    validate_relative_name(name)?;
    drop(file);
    fs::remove_file(PathBuf::from(format!("/proc/self/fd/{}", parent.as_raw_fd())).join(name))
        .map_err(|_| ProtectedBlobError::new("protected_blob_cleanup_failed"))
}

#[cfg(windows)]
fn create_relative_file(
    parent: &File,
    name: &str,
    apply_production_security: bool,
) -> Result<Option<File>, ProtectedBlobError> {
    nt_open_relative(parent, name, FILE_CREATE, true, apply_production_security)
}

#[cfg(windows)]
fn open_relative_file(
    parent: &File,
    name: &str,
    delete: bool,
) -> Result<Option<File>, ProtectedBlobError> {
    nt_open_relative(parent, name, FILE_OPEN, delete, false)
}

#[cfg(windows)]
struct LocalSecurityDescriptor(PSECURITY_DESCRIPTOR);

#[cfg(windows)]
impl LocalSecurityDescriptor {
    fn from_sddl(value: &str) -> Result<Self, ProtectedBlobError> {
        let words = value
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
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
            return Err(ProtectedBlobError::new(
                "protected_blob_file_security_descriptor_invalid",
            ));
        }
        Ok(Self(descriptor))
    }
}

#[cfg(windows)]
impl Drop for LocalSecurityDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                LocalFree(self.0.cast());
            }
        }
    }
}

#[cfg(windows)]
fn nt_open_relative(
    parent: &File,
    name: &str,
    disposition: u32,
    delete: bool,
    apply_production_security: bool,
) -> Result<Option<File>, ProtectedBlobError> {
    validate_relative_name(name)?;
    let mut words = name.encode_utf16().collect::<Vec<_>>();
    let byte_length = words
        .len()
        .checked_mul(size_of::<u16>())
        .and_then(|value| u16::try_from(value).ok())
        .ok_or_else(|| ProtectedBlobError::new("protected_blob_name_invalid"))?;
    let unicode = UNICODE_STRING {
        Length: byte_length,
        MaximumLength: byte_length,
        Buffer: words.as_mut_ptr(),
    };
    let explicit_security = if disposition == FILE_CREATE && apply_production_security {
        Some(LocalSecurityDescriptor::from_sddl(RUNTIME_BLOB_FILE_SDDL)?)
    } else {
        None
    };
    let attributes = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.as_raw_handle().cast(),
        ObjectName: &unicode,
        Attributes: 0,
        SecurityDescriptor: explicit_security
            .as_ref()
            .map_or(ptr::null_mut(), |descriptor| descriptor.0.cast()),
        SecurityQualityOfService: ptr::null_mut(),
    };
    let mut status_block = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let mut handle = ptr::null_mut();
    let desired = if disposition == FILE_CREATE {
        BLOB_FILE_CREATE_ACCESS
    } else if delete {
        BLOB_FILE_CLEANUP_ACCESS
    } else {
        BLOB_FILE_READ_ACCESS
    };
    let status = unsafe {
        NtCreateFile(
            &mut handle,
            desired,
            &attributes,
            &mut status_block,
            ptr::null(),
            FILE_ATTRIBUTE_NORMAL,
            0,
            disposition,
            FILE_SYNCHRONOUS_IO_NONALERT
                | FILE_NON_DIRECTORY_FILE
                | FILE_OPEN_REPARSE_POINT
                | FILE_WRITE_THROUGH,
            ptr::null(),
            0,
        )
    };
    if disposition == FILE_CREATE && status == STATUS_OBJECT_NAME_COLLISION {
        return Ok(None);
    }
    if disposition == FILE_OPEN
        && matches!(
            status,
            STATUS_NO_SUCH_FILE | STATUS_OBJECT_NAME_NOT_FOUND | STATUS_OBJECT_PATH_NOT_FOUND
        )
    {
        return Ok(None);
    }
    let expected = if disposition == FILE_CREATE {
        FILE_CREATED_INFORMATION
    } else {
        FILE_OPENED_INFORMATION
    };
    if status < 0
        || handle.is_null()
        || handle == INVALID_HANDLE_VALUE
        || status_block.Information != expected
    {
        if !handle.is_null() && handle != INVALID_HANDLE_VALUE {
            unsafe { drop(File::from_raw_handle(handle as RawHandle)) };
        }
        return Err(ProtectedBlobError::new(if disposition == FILE_CREATE {
            "protected_blob_create_failed"
        } else {
            "protected_blob_open_failed"
        }));
    }
    let file = unsafe { File::from_raw_handle(handle as RawHandle) };
    if handle_granted_access(&file)? != desired {
        return Err(ProtectedBlobError::new(
            "protected_blob_file_access_mismatch",
        ));
    }
    Ok(Some(file))
}

#[cfg(windows)]
fn handle_granted_access(file: &File) -> Result<u32, ProtectedBlobError> {
    #[repr(C)]
    struct PublicObjectBasicInformation {
        attributes: u32,
        granted_access: u32,
        handle_count: u32,
        pointer_count: u32,
        reserved: [u32; 10],
    }
    let mut basic = unsafe { zeroed::<PublicObjectBasicInformation>() };
    let mut returned = 0u32;
    let status = unsafe {
        NtQueryObject(
            file.as_raw_handle().cast(),
            ObjectBasicInformation,
            (&mut basic as *mut PublicObjectBasicInformation).cast(),
            size_of::<PublicObjectBasicInformation>() as u32,
            &mut returned,
        )
    };
    if status < 0 || returned < (size_of::<u32>() * 2) as u32 {
        return Err(ProtectedBlobError::new(
            "protected_blob_file_access_unavailable",
        ));
    }
    Ok(basic.granted_access)
}

#[cfg(windows)]
fn delete_relative_file(parent: &File, name: &str, file: File) -> Result<(), ProtectedBlobError> {
    validate_relative_name(name)?;
    let disposition = FILE_DISPOSITION_INFORMATION_EX {
        Flags: FILE_DISPOSITION_DELETE,
    };
    let mut status = unsafe { zeroed::<IO_STATUS_BLOCK>() };
    let result = unsafe {
        NtSetInformationFile(
            file.as_raw_handle().cast(),
            &mut status,
            (&disposition as *const FILE_DISPOSITION_INFORMATION_EX).cast(),
            size_of::<FILE_DISPOSITION_INFORMATION_EX>() as u32,
            FileDispositionInformationEx,
        )
    };
    drop(file);
    if result < 0 || open_relative_file(parent, name, false)?.is_some() {
        return Err(ProtectedBlobError::new("protected_blob_cleanup_failed"));
    }
    Ok(())
}

fn validate_relative_name(name: &str) -> Result<(), ProtectedBlobError> {
    if name.is_empty()
        || name.len() > 240
        || name.contains(['/', '\\', '\0'])
        || name == "."
        || name == ".."
    {
        return Err(ProtectedBlobError::new("protected_blob_name_invalid"));
    }
    Ok(())
}

fn is_zero(value: &BlobDigest) -> bool {
    *value == ZERO_DIGEST
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

#[cfg(test)]
#[path = "primitive_evidence_authority_blob/tests.rs"]
mod tests;
