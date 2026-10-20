"""Windows regression for the tray's force-exit path.

The harness deliberately extracts the production BackendJob and force_exit_desktop
implementations.  This keeps the test tied to the real process/job contract while
avoiding a Tauri build and any installed application state.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _rust_item(source: str, marker: str) -> str:
    """Return one Rust item, including its cfg attribute, using brace balancing."""
    start = source.find(marker)
    assert start >= 0, f"production extraction red: missing {marker!r}"
    cfg = source.rfind("#[cfg(windows)]", 0, start)
    assert cfg >= 0 and source[cfg:start].strip() == "#[cfg(windows)]", (
        f"production extraction red: {marker!r} is not cfg(windows)"
    )
    open_brace = source.find("{", start)
    assert open_brace >= 0, f"production extraction red: no body for {marker!r}"
    depth = 0
    in_string = False
    escaped = False
    for index in range(open_brace, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[cfg : index + 1]
    raise AssertionError(f"production extraction red: unterminated {marker!r}")


def _tasklist_has(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    return f'"{pid}"' in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="tray force-exit is Windows-specific")
def test_tray_force_exit_closes_job_tree_without_waiting_for_cleanup(tmp_path: Path) -> None:
    backend_source = (ROOT / "src-tauri/src/backend.rs").read_text(encoding="utf-8")
    main_source = (ROOT / "src-tauri/src/main.rs").read_text(encoding="utf-8")
    backend_job = _rust_item(backend_source, "pub(crate) struct BackendJob")
    backend_send = _rust_item(backend_source, "unsafe impl Send for BackendJob")
    backend_impl = _rust_item(backend_source, "impl BackendJob")
    backend_drop = _rust_item(backend_source, "impl Drop for BackendJob")
    force_exit = _rust_item(main_source, "fn force_exit_desktop() -> !")
    assert "TerminateProcess" in force_exit and "std::process::exit(0)" in force_exit, (
        "production extraction red: force_exit_desktop must self-terminate on Windows"
    )

    run_dir = tmp_path / "tray-force-exit-harness"
    src_dir = run_dir / "src"
    src_dir.mkdir(parents=True)
    (run_dir / "Cargo.toml").write_text(
        """[package]\nname = \"tray-force-exit-harness\"\nversion = \"0.1.0\"\nedition = \"2021\"\n\n[dependencies]\nwindows-sys = { version = \"0.59\", features = [\"Win32_Foundation\", \"Win32_Security\", \"Win32_System_JobObjects\", \"Win32_System_Threading\"] }\n""",
        encoding="utf-8",
    )

    # The extracted production items use the same names/imports as backend.rs.
    items = "\n\n".join(
        item.replace("pub(crate)", "pub")
        for item in (backend_job, backend_send, backend_impl, backend_drop)
    )
    harness = f"""#![cfg(windows)]
use std::fs;
use std::path::PathBuf;
use std::process::{{Child, Command}};
use std::sync::{{Arc, Mutex}};
use std::thread;
use std::time::Duration;
use std::os::windows::process::CommandExt;
use std::os::windows::io::AsRawHandle;
use windows_sys::Win32::Foundation::HANDLE;
use windows_sys::Win32::System::JobObjects::{{AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE}};
use windows_sys::Win32::Foundation::CloseHandle;

{items}

{force_exit}

fn write_pid(path: &PathBuf) {{ fs::write(path, std::process::id().to_string()).unwrap(); }}

fn main() {{
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("--sentinel") {{
        write_pid(&PathBuf::from(&args[2]));
        thread::sleep(Duration::from_secs(30));
        return;
    }}
    if args.get(1).map(String::as_str) == Some("--grandchild") {{
        write_pid(&PathBuf::from(&args[2]));
        thread::sleep(Duration::from_secs(30));
        return;
    }}
    if args.get(1).map(String::as_str) == Some("--child") {{
        write_pid(&PathBuf::from(&args[2]));
        let gate_deadline = std::time::Instant::now() + Duration::from_secs(8);
        while !PathBuf::from(&args[4]).exists() && std::time::Instant::now() < gate_deadline {{ thread::sleep(Duration::from_millis(10)); }}
        if !PathBuf::from(&args[4]).exists() {{ return; }}
        let mut grandchild = Command::new(std::env::current_exe().unwrap())
            .arg("--grandchild").arg(&args[3]).creation_flags(0x08000000).spawn().unwrap();
        let _ = grandchild.wait();
        return;
    }}

    let child_ready = PathBuf::from(&args[2]);
    let grandchild_ready = PathBuf::from(&args[3]);
    let root_ready = PathBuf::from(&args[4]);
    let lock_ready = PathBuf::from(&args[5]);
    let child_gate = PathBuf::from(&args[6]);
    let child = Command::new(std::env::current_exe().unwrap())
        .arg("--child").arg(&child_ready).arg(&grandchild_ready)
        .arg(&child_gate)
        .creation_flags(0x08000000).spawn().unwrap();
    let startup_deadline = std::time::Instant::now() + Duration::from_secs(8);
    while !child_ready.exists() && std::time::Instant::now() < startup_deadline {{ thread::sleep(Duration::from_millis(10)); }}
    if !child_ready.exists() {{ return; }}
    let job = BackendJob::assign(&child).unwrap();
    let held = Arc::new(Mutex::new(Some(job)));
    let held_for_cleanup = Arc::clone(&held);
    let lock_ready_for_cleanup = lock_ready.clone();
    thread::spawn(move || {{
        let _guard = held_for_cleanup.lock().unwrap();
        write_pid(&lock_ready_for_cleanup);
        thread::sleep(Duration::from_secs(30));
    }});
    while !lock_ready.exists() && std::time::Instant::now() < startup_deadline {{ thread::sleep(Duration::from_millis(10)); }}
    if !lock_ready.exists() {{ return; }}
    fs::write(&child_gate, "go").unwrap();
    while !grandchild_ready.exists() && std::time::Instant::now() < startup_deadline {{ thread::sleep(Duration::from_millis(10)); }}
    if !grandchild_ready.exists() {{ return; }}
    let persistence_blocker = thread::spawn(|| thread::sleep(Duration::from_secs(30)));
    write_pid(&root_ready);
    thread::sleep(Duration::from_millis(250));
    let _ = persistence_blocker.thread().id();
    force_exit_desktop();
}}
"""
    (src_dir / "main.rs").write_text(harness, encoding="utf-8")

    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(run_dir / "target")
    built = subprocess.run(
        ["cargo", "build", "--release", "--offline"],
        cwd=run_dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert built.returncode == 0, built.stdout + built.stderr

    def path(name: str) -> Path:
        return run_dir / f"{name}.pid"

    root = sentinel = backend_sentinel = None
    try:
        exe = run_dir / "target/release/tray-force-exit-harness.exe"
        sentinel = subprocess.Popen([str(exe), "--sentinel", str(path("sentinel"))], creationflags=subprocess.CREATE_NO_WINDOW)
        backend_sentinel = subprocess.Popen([str(exe), "--sentinel", str(path("backend-sentinel"))], creationflags=subprocess.CREATE_NO_WINDOW)
        root = subprocess.Popen([str(exe), "--root", str(path("child")), str(path("grandchild")), str(path("root")), str(path("lock")), str(path("gate"))], creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not path("root").exists():
            time.sleep(0.02)
        assert path("root").exists(), "harness did not reach force-exit readiness"
        ready_at = time.perf_counter()
        child_pid = int(path("child").read_text())
        grandchild_pid = int(path("grandchild").read_text())
        root_pid = int(path("root").read_text())
        assert root.wait(timeout=5) == 0
        assert time.perf_counter() - ready_at < 2.5, "force exit exceeded bounded shutdown time"
        gone_deadline = time.monotonic() + 3
        while time.monotonic() < gone_deadline and (_tasklist_has(child_pid) or _tasklist_has(grandchild_pid)):
            time.sleep(0.05)
        assert not _tasklist_has(root_pid)
        assert not _tasklist_has(child_pid), "job child survived process termination"
        assert not _tasklist_has(grandchild_pid), "job grandchild survived process termination"
        assert sentinel.poll() is None and backend_sentinel.poll() is None, "unowned sentinel was killed"
    finally:
        for process in (root, sentinel, backend_sentinel):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
