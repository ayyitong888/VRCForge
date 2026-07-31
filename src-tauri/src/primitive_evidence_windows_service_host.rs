#[cfg(windows)]
mod windows {
    use std::{
        ffi::OsString,
        os::windows::ffi::{OsStrExt, OsStringExt},
        ptr,
        sync::{
            atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering},
            OnceLock,
        },
    };
    use windows_sys::Win32::{
        Foundation::{ERROR_CALL_NOT_IMPLEMENTED, NO_ERROR},
        System::Services::{
            RegisterServiceCtrlHandlerExW, SetServiceStatus, StartServiceCtrlDispatcherW,
            SERVICE_ACCEPT_STOP, SERVICE_CONTROL_INTERROGATE, SERVICE_CONTROL_STOP,
            SERVICE_RUNNING, SERVICE_START_PENDING, SERVICE_STATUS, SERVICE_STATUS_HANDLE,
            SERVICE_STOPPED, SERVICE_STOP_PENDING, SERVICE_TABLE_ENTRYW, SERVICE_WIN32_OWN_PROCESS,
        },
    };

    const MAX_SERVICE_MAIN_ARGUMENTS: u32 = 6;
    const MAX_SERVICE_MAIN_ARGUMENT_WORDS: usize = 512;

    #[derive(Clone, Copy)]
    enum ServiceBody {
        // The same source module is compiled independently into the helper and
        // runtime service binaries; only the helper uses this variant.
        #[allow(dead_code)]
        NoArguments(fn() -> u32),
        // Only the protected runtime service consumes SCM start arguments.
        #[allow(dead_code)]
        StartArguments(fn(&[OsString]) -> u32),
    }

    static SERVICE_NAME: OnceLock<Vec<u16>> = OnceLock::new();
    static SERVICE_BODY: OnceLock<ServiceBody> = OnceLock::new();
    static STATUS_HANDLE: AtomicUsize = AtomicUsize::new(0);
    static STOP_REQUESTED: AtomicBool = AtomicBool::new(false);
    static CANDIDATE_VALIDATION_COMPLETE: AtomicBool = AtomicBool::new(false);
    static CURRENT_STATE: AtomicU32 = AtomicU32::new(SERVICE_STOPPED);
    static START_PENDING_CHECKPOINT: AtomicU32 = AtomicU32::new(0);

    #[allow(dead_code)]
    pub(super) fn run(service_name: &str, body: fn() -> u32) -> Result<(), &'static str> {
        run_inner(service_name, ServiceBody::NoArguments(body))
    }

    #[allow(dead_code)]
    pub(super) fn run_with_start_arguments(
        service_name: &str,
        body: fn(&[OsString]) -> u32,
    ) -> Result<(), &'static str> {
        run_inner(service_name, ServiceBody::StartArguments(body))
    }

    fn run_inner(service_name: &str, body: ServiceBody) -> Result<(), &'static str> {
        STOP_REQUESTED.store(false, Ordering::Release);
        CANDIDATE_VALIDATION_COMPLETE.store(false, Ordering::Release);
        STATUS_HANDLE.store(0, Ordering::Release);
        CURRENT_STATE.store(SERVICE_STOPPED, Ordering::Release);
        START_PENDING_CHECKPOINT.store(0, Ordering::Release);
        if service_name.is_empty() || service_name.encode_utf16().any(|word| word == 0) {
            return Err("authority_service_host_name_invalid");
        }
        let encoded = OsString::from(service_name)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        SERVICE_NAME
            .set(encoded)
            .map_err(|_| "authority_service_host_already_started")?;
        SERVICE_BODY
            .set(body)
            .map_err(|_| "authority_service_host_already_started")?;
        let name = SERVICE_NAME
            .get()
            .ok_or("authority_service_host_state_invalid")?;
        let table = [
            SERVICE_TABLE_ENTRYW {
                lpServiceName: name.as_ptr().cast_mut(),
                lpServiceProc: Some(service_main),
            },
            SERVICE_TABLE_ENTRYW {
                lpServiceName: ptr::null_mut(),
                lpServiceProc: None,
            },
        ];
        if unsafe { StartServiceCtrlDispatcherW(table.as_ptr()) } == 0 {
            return Err("authority_service_dispatcher_start_failed");
        }
        Ok(())
    }

    unsafe extern "system" fn service_main(argc: u32, argv: *mut *mut u16) {
        let Some(name) = SERVICE_NAME.get() else {
            return;
        };
        let status_handle = unsafe {
            RegisterServiceCtrlHandlerExW(
                name.as_ptr(),
                Some(control_handler),
                ptr::null::<core::ffi::c_void>(),
            )
        };
        if status_handle.is_null() {
            return;
        }
        STATUS_HANDLE.store(status_handle as usize, Ordering::Release);
        if !report_status(status_handle, SERVICE_START_PENDING, 0, 1, 30_000) {
            return;
        }
        START_PENDING_CHECKPOINT.store(1, Ordering::Release);
        let body_exit_code = match (SERVICE_BODY.get().copied(), unsafe {
            capture_service_start_arguments(argc, argv, name)
        }) {
            (Some(ServiceBody::NoArguments(body)), Ok(arguments)) if arguments.is_empty() => body(),
            (Some(ServiceBody::StartArguments(body)), Ok(arguments)) => body(&arguments),
            _ => 2,
        };
        let exit_code = final_exit_code(
            body_exit_code,
            current_state(),
            CANDIDATE_VALIDATION_COMPLETE.load(Ordering::Acquire),
        );
        let _ = report_status(status_handle, SERVICE_STOPPED, exit_code, 0, 0);
        STATUS_HANDLE.store(0, Ordering::Release);
        CANDIDATE_VALIDATION_COMPLETE.store(false, Ordering::Release);
        START_PENDING_CHECKPOINT.store(0, Ordering::Release);
    }

    unsafe fn capture_service_start_arguments(
        argc: u32,
        argv: *mut *mut u16,
        expected_service_name: &[u16],
    ) -> Result<Vec<OsString>, &'static str> {
        if argc == 0 || argc > MAX_SERVICE_MAIN_ARGUMENTS || argv.is_null() {
            return Err("authority_service_host_start_arguments_invalid");
        }
        let raw = unsafe { std::slice::from_raw_parts(argv, argc as usize) };
        let mut decoded = Vec::with_capacity(raw.len());
        for value in raw {
            if value.is_null() {
                return Err("authority_service_host_start_arguments_invalid");
            }
            let mut length = 0usize;
            while length < MAX_SERVICE_MAIN_ARGUMENT_WORDS {
                if unsafe { *value.add(length) } == 0 {
                    break;
                }
                length += 1;
            }
            if length == MAX_SERVICE_MAIN_ARGUMENT_WORDS {
                return Err("authority_service_host_start_arguments_invalid");
            }
            decoded.push(OsString::from_wide(unsafe {
                std::slice::from_raw_parts(*value, length)
            }));
        }
        let expected = expected_service_name
            .strip_suffix(&[0])
            .ok_or("authority_service_host_state_invalid")?;
        if decoded.first() != Some(&OsString::from_wide(expected)) {
            return Err("authority_service_host_start_service_name_invalid");
        }
        Ok(decoded.into_iter().skip(1).collect())
    }

    unsafe extern "system" fn control_handler(
        control: u32,
        _event_type: u32,
        _event_data: *mut core::ffi::c_void,
        _context: *mut core::ffi::c_void,
    ) -> u32 {
        match control {
            SERVICE_CONTROL_INTERROGATE => NO_ERROR,
            SERVICE_CONTROL_STOP => {
                if current_state() != SERVICE_RUNNING {
                    return ERROR_CALL_NOT_IMPLEMENTED;
                }
                STOP_REQUESTED.store(true, Ordering::Release);
                let raw = STATUS_HANDLE.load(Ordering::Acquire);
                if raw != 0 {
                    let _ = report_status(
                        raw as SERVICE_STATUS_HANDLE,
                        SERVICE_STOP_PENDING,
                        0,
                        1,
                        30_000,
                    );
                }
                NO_ERROR
            }
            _ => ERROR_CALL_NOT_IMPLEMENTED,
        }
    }

    fn report_status(
        status_handle: SERVICE_STATUS_HANDLE,
        current_state: u32,
        exit_code: u32,
        checkpoint: u32,
        wait_hint: u32,
    ) -> bool {
        let status = SERVICE_STATUS {
            dwServiceType: SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState: current_state,
            dwControlsAccepted: controls_accepted(current_state),
            dwWin32ExitCode: exit_code,
            dwServiceSpecificExitCode: 0,
            dwCheckPoint: checkpoint,
            dwWaitHint: wait_hint,
        };
        if unsafe { SetServiceStatus(status_handle, &status) } == 0 {
            return false;
        }
        CURRENT_STATE.store(current_state, Ordering::Release);
        true
    }

    pub(super) fn stop_requested() -> bool {
        STOP_REQUESTED.load(Ordering::Acquire)
    }

    pub(super) fn report_running() -> Result<(), &'static str> {
        if current_state() != SERVICE_START_PENDING || stop_requested() {
            return Err("authority_service_host_readiness_phase_invalid");
        }
        let raw = STATUS_HANDLE.load(Ordering::Acquire);
        if raw == 0 {
            return Err("authority_service_host_status_handle_unavailable");
        }
        if !report_status(raw as SERVICE_STATUS_HANDLE, SERVICE_RUNNING, 0, 0, 0) {
            return Err("authority_service_host_readiness_report_failed");
        }
        Ok(())
    }

    #[allow(dead_code)]
    pub(super) fn advance_candidate_start_pending_checkpoint(
        checkpoint: u32,
        wait_hint_millis: u32,
    ) -> Result<(), &'static str> {
        let previous = START_PENDING_CHECKPOINT.load(Ordering::Acquire);
        validate_candidate_start_pending_checkpoint(
            current_state(),
            stop_requested(),
            previous,
            checkpoint,
            wait_hint_millis,
        )?;
        START_PENDING_CHECKPOINT
            .compare_exchange(previous, checkpoint, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "authority_service_host_candidate_checkpoint_raced")?;
        let raw = STATUS_HANDLE.load(Ordering::Acquire);
        if raw == 0 {
            let _ = START_PENDING_CHECKPOINT.compare_exchange(
                checkpoint,
                previous,
                Ordering::AcqRel,
                Ordering::Acquire,
            );
            return Err("authority_service_host_status_handle_unavailable");
        }
        if !report_status(
            raw as SERVICE_STATUS_HANDLE,
            SERVICE_START_PENDING,
            0,
            checkpoint,
            wait_hint_millis,
        ) {
            let _ = START_PENDING_CHECKPOINT.compare_exchange(
                checkpoint,
                previous,
                Ordering::AcqRel,
                Ordering::Acquire,
            );
            return Err("authority_service_host_candidate_checkpoint_failed");
        }
        Ok(())
    }

    fn validate_candidate_start_pending_checkpoint(
        state: u32,
        stop_requested: bool,
        previous: u32,
        checkpoint: u32,
        wait_hint_millis: u32,
    ) -> Result<(), &'static str> {
        if state != SERVICE_START_PENDING || stop_requested {
            return Err("authority_service_host_candidate_phase_invalid");
        }
        if previous == 0 || checkpoint <= previous {
            return Err("authority_service_host_candidate_checkpoint_invalid");
        }
        if wait_hint_millis == 0 || wait_hint_millis > 120_000 {
            return Err("authority_service_host_candidate_wait_hint_invalid");
        }
        Ok(())
    }

    #[allow(dead_code)]
    pub(super) fn mark_candidate_validation_complete() -> Result<(), &'static str> {
        if current_state() != SERVICE_START_PENDING || stop_requested() {
            return Err("authority_service_host_candidate_phase_invalid");
        }
        CANDIDATE_VALIDATION_COMPLETE
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "authority_service_host_candidate_already_complete")?;
        Ok(())
    }

    fn current_state() -> u32 {
        CURRENT_STATE.load(Ordering::Acquire)
    }

    fn final_exit_code(body_exit_code: u32, state: u32, candidate_complete: bool) -> u32 {
        if body_exit_code == 0 && state == SERVICE_START_PENDING && !candidate_complete {
            2
        } else {
            body_exit_code
        }
    }

    fn controls_accepted(current_state: u32) -> u32 {
        if current_state == SERVICE_RUNNING {
            SERVICE_ACCEPT_STOP
        } else {
            0
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn stop_is_rejected_before_readiness_and_accepted_only_while_running() {
            STOP_REQUESTED.store(false, Ordering::Release);
            CANDIDATE_VALIDATION_COMPLETE.store(false, Ordering::Release);
            STATUS_HANDLE.store(0, Ordering::Release);
            CURRENT_STATE.store(SERVICE_START_PENDING, Ordering::Release);
            assert_eq!(controls_accepted(SERVICE_START_PENDING), 0);
            assert_eq!(controls_accepted(SERVICE_STOP_PENDING), 0);
            assert_eq!(controls_accepted(SERVICE_RUNNING), SERVICE_ACCEPT_STOP);
            assert_eq!(
                unsafe {
                    control_handler(SERVICE_CONTROL_STOP, 0, ptr::null_mut(), ptr::null_mut())
                },
                ERROR_CALL_NOT_IMPLEMENTED
            );
            assert!(!stop_requested());
            CURRENT_STATE.store(SERVICE_RUNNING, Ordering::Release);
            assert_eq!(
                unsafe {
                    control_handler(SERVICE_CONTROL_STOP, 0, ptr::null_mut(), ptr::null_mut())
                },
                NO_ERROR
            );
            assert!(stop_requested());
        }

        #[test]
        fn readiness_is_explicit_and_missing_readiness_cannot_report_success() {
            STOP_REQUESTED.store(false, Ordering::Release);
            STATUS_HANDLE.store(0, Ordering::Release);
            CURRENT_STATE.store(SERVICE_START_PENDING, Ordering::Release);
            assert_eq!(
                report_running(),
                Err("authority_service_host_status_handle_unavailable")
            );
            assert_eq!(final_exit_code(0, SERVICE_START_PENDING, false), 2);
            assert_eq!(final_exit_code(7, SERVICE_START_PENDING, false), 7);
            assert_eq!(final_exit_code(0, SERVICE_RUNNING, false), 0);
            assert_eq!(mark_candidate_validation_complete(), Ok(()));
            assert_eq!(
                mark_candidate_validation_complete(),
                Err("authority_service_host_candidate_already_complete")
            );
            assert_eq!(final_exit_code(0, SERVICE_START_PENDING, true), 0);
        }

        #[test]
        fn candidate_checkpoint_is_monotonic_bounded_and_start_pending_only() {
            assert_eq!(
                validate_candidate_start_pending_checkpoint(
                    SERVICE_START_PENDING,
                    false,
                    1,
                    2,
                    30_000,
                ),
                Ok(())
            );
            assert_eq!(
                validate_candidate_start_pending_checkpoint(
                    SERVICE_START_PENDING,
                    false,
                    2,
                    2,
                    30_000,
                ),
                Err("authority_service_host_candidate_checkpoint_invalid")
            );
            assert_eq!(
                validate_candidate_start_pending_checkpoint(SERVICE_RUNNING, false, 2, 3, 30_000,),
                Err("authority_service_host_candidate_phase_invalid")
            );
            assert_eq!(
                validate_candidate_start_pending_checkpoint(
                    SERVICE_START_PENDING,
                    true,
                    2,
                    3,
                    30_000,
                ),
                Err("authority_service_host_candidate_phase_invalid")
            );
            assert_eq!(
                validate_candidate_start_pending_checkpoint(SERVICE_START_PENDING, false, 2, 3, 0,),
                Err("authority_service_host_candidate_wait_hint_invalid")
            );
            assert_eq!(
                validate_candidate_start_pending_checkpoint(
                    SERVICE_START_PENDING,
                    false,
                    2,
                    3,
                    120_001,
                ),
                Err("authority_service_host_candidate_wait_hint_invalid")
            );
        }

        #[test]
        fn scm_start_arguments_are_bounded_and_exclude_the_service_name() {
            fn wide(value: &str) -> Vec<u16> {
                OsString::from(value)
                    .encode_wide()
                    .chain(std::iter::once(0))
                    .collect()
            }

            let expected = wide("VRCForgePrimitiveEvidence");
            let mut values = [
                wide("VRCForgePrimitiveEvidence"),
                wide("--candidate-validation-v1"),
                wide("--transaction-sha256=11"),
            ];
            let mut pointers = values
                .iter_mut()
                .map(|value| value.as_mut_ptr())
                .collect::<Vec<_>>();
            let actual = unsafe {
                capture_service_start_arguments(
                    pointers.len() as u32,
                    pointers.as_mut_ptr(),
                    &expected,
                )
            }
            .unwrap();
            assert_eq!(
                actual,
                [
                    OsString::from("--candidate-validation-v1"),
                    OsString::from("--transaction-sha256=11"),
                ]
            );

            values[0] = wide("WrongService");
            pointers[0] = values[0].as_mut_ptr();
            assert_eq!(
                unsafe {
                    capture_service_start_arguments(
                        pointers.len() as u32,
                        pointers.as_mut_ptr(),
                        &expected,
                    )
                },
                Err("authority_service_host_start_service_name_invalid")
            );
            assert_eq!(
                unsafe { capture_service_start_arguments(0, ptr::null_mut(), &expected) },
                Err("authority_service_host_start_arguments_invalid")
            );
        }
    }
}

#[cfg(windows)]
#[allow(dead_code)]
pub(crate) fn run_service_dispatcher(
    service_name: &str,
    body: fn() -> u32,
) -> Result<(), &'static str> {
    windows::run(service_name, body)
}

#[cfg(windows)]
#[allow(dead_code)]
pub(crate) fn run_service_dispatcher_with_start_arguments(
    service_name: &str,
    body: fn(&[std::ffi::OsString]) -> u32,
) -> Result<(), &'static str> {
    windows::run_with_start_arguments(service_name, body)
}

#[cfg(windows)]
pub(crate) fn stop_requested() -> bool {
    windows::stop_requested()
}

#[cfg(windows)]
pub(crate) fn report_running() -> Result<(), &'static str> {
    windows::report_running()
}

#[cfg(windows)]
#[allow(dead_code)]
pub(crate) fn advance_candidate_start_pending_checkpoint(
    checkpoint: u32,
    wait_hint_millis: u32,
) -> Result<(), &'static str> {
    windows::advance_candidate_start_pending_checkpoint(checkpoint, wait_hint_millis)
}

#[cfg(windows)]
#[allow(dead_code)]
pub(crate) fn mark_candidate_validation_complete() -> Result<(), &'static str> {
    windows::mark_candidate_validation_complete()
}

#[cfg(not(windows))]
pub(crate) fn run_service_dispatcher(
    _service_name: &str,
    _body: fn() -> u32,
) -> Result<(), &'static str> {
    Err("authority_service_host_platform_unsupported")
}

#[cfg(not(windows))]
pub(crate) fn run_service_dispatcher_with_start_arguments(
    _service_name: &str,
    _body: fn(&[std::ffi::OsString]) -> u32,
) -> Result<(), &'static str> {
    Err("authority_service_host_platform_unsupported")
}

#[cfg(not(windows))]
pub(crate) fn stop_requested() -> bool {
    false
}

#[cfg(not(windows))]
pub(crate) fn report_running() -> Result<(), &'static str> {
    Err("authority_service_host_platform_unsupported")
}

#[cfg(not(windows))]
pub(crate) fn advance_candidate_start_pending_checkpoint(
    _checkpoint: u32,
    _wait_hint_millis: u32,
) -> Result<(), &'static str> {
    Err("authority_service_host_platform_unsupported")
}

#[cfg(not(windows))]
pub(crate) fn mark_candidate_validation_complete() -> Result<(), &'static str> {
    Err("authority_service_host_platform_unsupported")
}
