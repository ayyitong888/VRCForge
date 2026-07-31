//! Exact held process epochs for the installed authority service and its parent.
//!
//! Process identifiers are discovered from the live process tree and are never
//! accepted from a request or policy document. Both processes remain held open
//! so PID reuse cannot change the policy source after admission.

use super::policy_source::HeldAuthorityProcessReadback;
use super::SupervisorError;
use crate::primitive_evidence_authority_install::bootstrap::AuthenticatedFinalCommitPolicyBinding;
use std::{
    mem::size_of,
    os::windows::io::{AsHandle, AsRawHandle, FromRawHandle, OwnedHandle},
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
            TH32CS_SNAPPROCESS,
        },
        Threading::{
            GetCurrentProcessId, GetProcessId, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        },
    },
};

const PROCESS_SYNCHRONIZE: u32 = 0x0010_0000;

/// Non-clone service-owned handles for the exact live service and parent
/// epochs. The parent handle is resolved before either handle is exposed.
#[derive(Debug)]
pub(crate) struct HeldAuthorityProcessHandles {
    service: OwnedHandle,
    parent: OwnedHandle,
}

impl HeldAuthorityProcessHandles {
    pub(crate) fn open_current_process_tree() -> Result<Self, SupervisorError> {
        let service_pid = unsafe { GetCurrentProcessId() };
        if service_pid == 0 {
            return Err(SupervisorError::new(
                "authority_policy_service_process_id_invalid",
            ));
        }
        let parent_pid = parent_process_id(service_pid)?;
        if parent_pid == 0 || parent_pid == service_pid {
            return Err(SupervisorError::new(
                "authority_policy_parent_process_id_invalid",
            ));
        }

        let service = open_process_epoch(service_pid)?;
        let parent = open_process_epoch(parent_pid)?;
        if unsafe { GetProcessId(service.as_raw_handle().cast()) } != service_pid
            || unsafe { GetProcessId(parent.as_raw_handle().cast()) } != parent_pid
        {
            return Err(SupervisorError::new(
                "authority_policy_process_handle_identity_mismatch",
            ));
        }
        Ok(Self { service, parent })
    }

    pub(crate) fn readback(
        &self,
        final_commit: &AuthenticatedFinalCommitPolicyBinding,
    ) -> Result<HeldAuthorityProcessReadback, SupervisorError> {
        HeldAuthorityProcessReadback::read_from_held_handles(
            final_commit,
            self.service.as_handle(),
            self.parent.as_handle(),
        )
    }

    #[cfg(test)]
    fn process_ids(&self) -> (u32, u32) {
        unsafe {
            (
                GetProcessId(self.service.as_raw_handle().cast()),
                GetProcessId(self.parent.as_raw_handle().cast()),
            )
        }
    }
}

fn open_process_epoch(process_id: u32) -> Result<OwnedHandle, SupervisorError> {
    let handle = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
            0,
            process_id,
        )
    };
    if handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(SupervisorError::new(
            "authority_policy_process_handle_open_failed",
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(handle.cast()) })
}

fn parent_process_id(service_pid: u32) -> Result<u32, SupervisorError> {
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(SupervisorError::new(
            "authority_policy_process_snapshot_failed",
        ));
    }
    let snapshot = SnapshotHandle(snapshot);
    let mut entry = PROCESSENTRY32W {
        dwSize: size_of::<PROCESSENTRY32W>() as u32,
        ..unsafe { std::mem::zeroed() }
    };
    let mut present = unsafe { Process32FirstW(snapshot.0, &mut entry) } != 0;
    while present {
        if entry.th32ProcessID == service_pid {
            return Ok(entry.th32ParentProcessID);
        }
        present = unsafe { Process32NextW(snapshot.0, &mut entry) } != 0;
    }
    Err(SupervisorError::new(
        "authority_policy_service_process_not_in_snapshot",
    ))
}

struct SnapshotHandle(HANDLE);

impl Drop for SnapshotHandle {
    fn drop(&mut self) {
        if self.0 != INVALID_HANDLE_VALUE && !self.0.is_null() {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_service_and_parent_epochs_are_discovered_and_held() {
        let handles = HeldAuthorityProcessHandles::open_current_process_tree().unwrap();
        let (service_pid, parent_pid) = handles.process_ids();
        assert_eq!(service_pid, unsafe { GetCurrentProcessId() });
        assert_ne!(service_pid, parent_pid);
        assert_ne!(parent_pid, 0);
    }
}
