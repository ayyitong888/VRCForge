"""Run the production Rust observer with a deterministic clock and owned child state."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def startup_observer_source(source: str) -> str:
    return source[source.index("#[derive(Debug, PartialEq, Eq)]\npub(crate) enum BackendStartupWait"):
                  source.index("pub(crate) fn backend_start_failure_payload(")]


def startup_replay_source(source: str) -> str:
    observer = startup_observer_source(source)
    begin = source[source.index("pub(crate) fn begin_backend_start("):
                   source.index("pub(crate) fn clear_backend_start_in_progress(")]
    call = "wait_for_backend_or_child_exit(&state, Duration::from_secs(18), || { warnings += 1; assert!(!begin_backend_start(&state).unwrap()); })" if "on_timeout:" in observer else "wait_for_backend_or_child_exit(&state, Duration::from_secs(18))"
    return r'''
use std::{sync::{Mutex, atomic::{AtomicU64, Ordering}}, time::Duration};
static CLOCK: AtomicU64 = AtomicU64::new(0);
static READY_AT: AtomicU64 = AtomicU64::new(u64::MAX);
struct Instant(u64);
impl Instant {
    fn now() -> Self { Self(CLOCK.load(Ordering::Acquire)) }
    fn elapsed(&self) -> Duration { Duration::from_millis(CLOCK.load(Ordering::Acquire) - self.0) }
}
mod thread {
    pub fn sleep(duration: std::time::Duration) { super::CLOCK.fetch_add(duration.as_millis() as u64, super::Ordering::AcqRel); }
}
struct Shutdown(u64);
impl Shutdown { fn load(&self, _: Ordering) -> bool { CLOCK.load(Ordering::Acquire) >= self.0 } }
struct Child { exit_at: u64, replace_at: u64 }
struct Exit;
impl Exit { fn code(&self) -> Option<i32> { Some(23) } }
impl Child {
    fn id(&self) -> u32 { if CLOCK.load(Ordering::Acquire) >= self.replace_at { 2 } else { 1 } }
    fn try_wait(&mut self) -> Result<Option<Exit>, std::io::Error> {
        Ok(if CLOCK.load(Ordering::Acquire) >= self.exit_at { Some(Exit) } else { None })
    }
}
struct BackendState { child: Mutex<Option<Child>>, shutdown_started: Shutdown, start_in_progress: Mutex<bool> }
fn backend_port_open() -> bool { CLOCK.load(Ordering::Acquire) >= READY_AT.load(Ordering::Acquire) }
#[allow(dead_code)]
fn managed_backend_exit_code(state: &BackendState) -> Result<Option<Option<i32>>, String> {
    Ok(state.child.lock().unwrap().as_mut().and_then(|c| c.try_wait().unwrap()).map(|e| e.code()))
}
''' + observer + begin + r'''
fn replay(ready: u64, exit: u64, stop: u64, replace: u64, expected: &str, warning_count: usize) {
    CLOCK.store(0, Ordering::Release); READY_AT.store(ready, Ordering::Release);
    let state = BackendState { child: Mutex::new(Some(Child { exit_at: exit, replace_at: replace })), shutdown_started: Shutdown(stop), start_in_progress: Mutex::new(false) };
    assert!(begin_backend_start(&state).unwrap());
    #[allow(unused_mut)] let mut warnings = 0;
    let outcome = ''' + call + r'''.unwrap();
    assert_eq!(format!("{outcome:?}"), expected);
    assert_eq!(warnings, warning_count);
    assert!(!begin_backend_start(&state).unwrap(), "observer must retain the startup owner until its worker settles");
    println!("{expected}: elapsed={}ms warnings={warnings}", CLOCK.load(Ordering::Acquire));
}
fn main() {
    let never = u64::MAX;
    replay(32_524, never, never, never, "Ready", 1);
    replay(3_000, never, never, never, "Ready", 0);
    replay(never, 20_000, never, never, "ChildExited(Some(23))", 1);
    replay(never, never, 20_000, never, "Stopped", 1);
    replay(never, never, never, 20_000, "Stopped", 1);
}
'''


def run_startup_replay(source: str, directory: Path) -> subprocess.CompletedProcess[str]:
    fixture = directory / "startup_observer.rs"
    executable = directory / "startup_observer.exe"
    fixture.write_text(startup_replay_source(source), encoding="utf-8")
    subprocess.run(["rustc", "--edition=2021", str(fixture), "-o", str(executable)], check=True, capture_output=True, text=True)
    return subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)


def test_owned_startup_survives_warning_until_ready_exit_or_stop(tmp_path: Path) -> None:
    result = run_startup_replay((ROOT / "src-tauri/src/backend.rs").read_text(encoding="utf-8"), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
