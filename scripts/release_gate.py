from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWN_FAILURES_PATH = REPO_ROOT / "tests" / "known_failures.txt"
COMPOSITION_CONTRACT_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "dashboard_composition_contract_v1.json"
)
FORBIDDEN_SYMBOLS = (
    "app",
    "AGENT_GATEWAY",
    "EVENT_BUS",
    "DASHBOARD_STATE",
    "DASHBOARD_RUNTIME",
)
CHARACTERIZATION_NODEIDS = (
    "tests/test_dashboard_composition.py::test_route_table_contract_matches_the_entry_baseline",
    "tests/test_dashboard_composition.py::test_openapi_contract_matches_the_entry_baseline",
    "tests/test_dashboard_composition.py::test_catch_all_agent_mcp_mount_is_registered_last",
    "tests/test_dashboard_composition.py::test_composition_root_calls_are_exactly_once",
    "tests/test_dashboard_composition.py::test_event_envelope_contract_keeps_exact_public_keys",
    "tests/test_dashboard_composition.py::test_atomic_disk_and_chat_rollback_contract_preserves_exact_bytes",
)
SOURCE_COPY_EXCLUDES = {
    ".git",
    ".pytest_cache",
    ".tmp",
    "LocalBuilds",
    "__pycache__",
    "artifacts",
    "node_modules",
    "target",
}
RUNTIME_ENVIRONMENT_NAMES = (
    "VRCFORGE_APP_DIR",
    "VRCFORGE_USER_DATA_DIR",
    "VRCFORGE_CONFIG_DIR",
    "VRCFORGE_CONFIG_PATH",
    "VRCFORGE_LOG_DIR",
    "VRCFORGE_ARTIFACTS_DIR",
    "VRCFORGE_DASHBOARD_DIR",
    "VRCFORGE_SETTINGS_PATH",
    "VRCFORGE_EXE",
    "VRCFORGE_AGENT_START_RUNTIME",
    "VRCFORGE_PRIMITIVE_LIVE_STDIN",
    "VRCFORGE_RUN_REAL_PTY_TESTS",
    "VRCFORGE_DISABLE_APP_AUTH",
    "VRCFORGE_TOKEN",
    "VRCFORGE_AGENT_TOKEN",
    "VRCFORGE_APP_SESSION_TOKEN",
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str


@dataclass(frozen=True)
class StepResult:
    ok: bool
    output: str


Step = tuple[str, Callable[[], StepResult]]


def run_command(
    command: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> CommandResult:
    process_env = os.environ.copy()
    blocked_names = {name.casefold() for name in RUNTIME_ENVIRONMENT_NAMES}
    for existing_key in tuple(process_env):
        if existing_key.casefold() in blocked_names:
            process_env.pop(existing_key)
    if env:
        for key, value in env.items():
            normalized_key = str(key)
            for existing_key in tuple(process_env):
                if existing_key.casefold() == normalized_key.casefold():
                    process_env.pop(existing_key)
            process_env[normalized_key] = str(value)
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return CommandResult(
            returncode=124,
            output=f"{partial}\nCommand timed out after {timeout:.1f} seconds.\n",
        )
    except OSError as exc:
        return CommandResult(returncode=127, output=f"{type(exc).__name__}: {exc}\n")
    return CommandResult(returncode=completed.returncode, output=completed.stdout or "")


def cleanup_directory(path: Path) -> str:
    if not path.exists():
        return ""

    def remove_readonly(
        function: Callable[[str], object],
        target: str,
        _error: object,
    ) -> None:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        function(target)

    try:
        shutil.rmtree(path, onexc=remove_readonly)
    except OSError as exc:
        return f"Isolation cleanup failed; residual path: {path}\n{type(exc).__name__}: {exc}\n"
    return ""


def read_known_failures(path: Path = KNOWN_FAILURES_PATH) -> set[str]:
    failures: set[str] = set()
    comments: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            comments.clear()
            continue
        if line.startswith("#"):
            comments.append(line[1:].strip())
            continue
        if not any(comment.startswith("Allow reason:") for comment in comments):
            raise ValueError(f"{path}:{line_number}: nodeid has no per-entry Allow reason comment")
        if not any(comment.startswith("Fix/remove after:") for comment in comments):
            raise ValueError(f"{path}:{line_number}: nodeid has no per-entry Fix/remove after comment")
        if line in failures:
            raise ValueError(f"{path}:{line_number}: duplicate nodeid: {line}")
        failures.add(line.replace("\\", "/"))
        comments.clear()
    if not failures:
        raise ValueError(f"{path}: whitelist is empty")
    return failures


def _testcase_nodeid(testcase: ET.Element) -> str:
    name = str(testcase.attrib.get("name") or "").strip()
    classname = str(testcase.attrib.get("classname") or "").strip()
    source_file = str(testcase.attrib.get("file") or "").strip().replace("\\", "/")
    if source_file:
        if not source_file.endswith(".py"):
            source_file = f"{source_file}.py"
        class_name = classname.rsplit(".", 1)[-1] if classname else ""
        if class_name and not class_name.startswith("test_"):
            return f"{source_file}::{class_name}::{name}"
        return f"{source_file}::{name}"
    parts = [part for part in classname.split(".") if part]
    if not parts:
        return name
    if parts[-1].startswith("test_"):
        module_parts = parts
        class_name = ""
    else:
        module_parts = parts[:-1]
        class_name = parts[-1]
    module_path = "/".join(module_parts) + ".py"
    if class_name:
        return f"{module_path}::{class_name}::{name}"
    return f"{module_path}::{name}"


def read_pytest_failures(xml_path: Path) -> set[str]:
    root = ET.parse(xml_path).getroot()
    failures: set[str] = set()
    for testcase in root.iter("testcase"):
        if testcase.find("failure") is None and testcase.find("error") is None:
            continue
        failures.add(_testcase_nodeid(testcase).replace("\\", "/"))
    return failures


def compare_failure_sets(actual: set[str], expected: set[str]) -> StepResult:
    added = sorted(actual - expected)
    removed = sorted(expected - actual)
    lines = [
        f"Observed failure count: {len(actual)}",
        f"Whitelisted failure count: {len(expected)}",
        "Additional failures (多出來的):",
    ]
    lines.extend(f"  {nodeid}" for nodeid in added)
    if not added:
        lines.append("  (none)")
    lines.append("Missing failures (少掉的):")
    lines.extend(f"  {nodeid}" for nodeid in removed)
    if not removed:
        lines.append("  (none)")
    lines.append("Failure set matches whitelist exactly." if not added and not removed else "Failure set does not match whitelist.")
    return StepResult(ok=not added and not removed, output="\n".join(lines) + "\n")


def tracked_pytest_targets() -> tuple[str, ...]:
    result = run_command(["git", "ls-files", "tests"])
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files tests failed:\n{result.output}")
    return tuple(
        sorted(
            path.replace("\\", "/")
            for path in result.output.splitlines()
            if Path(path).name.startswith("test_") and Path(path).suffix == ".py"
        )
    )


def full_pytest_step() -> StepResult:
    targets = tracked_pytest_targets()
    temp_root = Path(tempfile.mkdtemp(prefix="vrcforge-release-gate-pytest-"))
    xml_path = temp_root / "pytest.xml"
    result = run_command(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(temp_root / "pytest-temp"),
            "--junitxml",
            str(xml_path),
            *targets,
        ],
        env={
            "LOCALAPPDATA": str(temp_root / "localappdata"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    output = result.output
    ok = result.returncode in {0, 1}
    if not xml_path.is_file():
        output += f"\npytest did not create JUnit XML: {xml_path}\n"
        ok = False
    else:
        try:
            expected = read_known_failures()
            actual = read_pytest_failures(xml_path)
            comparison = compare_failure_sets(actual, expected)
            output += "\n" + comparison.output
            ok = ok and comparison.ok
        except (OSError, ValueError, ET.ParseError) as exc:
            output += f"\nWhitelist/JUnit evaluation failed: {type(exc).__name__}: {exc}\n"
            ok = False
    cleanup_error = cleanup_directory(temp_root)
    if cleanup_error:
        output += "\n" + cleanup_error
        ok = False
    return StepResult(ok=ok, output=output)


def command_step(
    command: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> StepResult:
    result = run_command(command, cwd=cwd, env=env, timeout=timeout)
    return StepResult(ok=result.returncode == 0, output=result.output)


def characterization_step() -> StepResult:
    return command_step(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *CHARACTERIZATION_NODEIDS,
        ]
    )


def tsc_step() -> StepResult:
    executable = "npx.cmd" if os.name == "nt" else "npx"
    return command_step([executable, "--no-install", "tsc", "--noEmit"])


def cargo_check_step() -> StepResult:
    temp_root = Path(tempfile.mkdtemp(prefix="vrcforge-release-gate-cargo-"))
    source = temp_root / "source"
    output = f"Isolated Cargo source: {source}\n"
    ok = False
    try:
        _copy_source_tree(source)
        result = run_command(
            ["cargo", "check", "--locked"],
            cwd=source / "src-tauri",
            env={"CARGO_TARGET_DIR": str(temp_root / "target")},
        )
        output += result.output
        ok = result.returncode == 0
    except (OSError, shutil.Error) as exc:
        output += f"Cargo isolation setup failed: {type(exc).__name__}: {exc}\n"
    cleanup_error = cleanup_directory(temp_root)
    if cleanup_error:
        output += "\n" + cleanup_error
        ok = False
    return StepResult(ok=ok, output=output)


def _git_added_python_paths() -> tuple[set[str], str]:
    commands = (
        ["git", "diff", "--name-only", "--diff-filter=A", "HEAD", "--", "*.py"],
        ["git", "diff", "--cached", "--name-only", "--diff-filter=A", "HEAD", "--", "*.py"],
        ["git", "ls-files", "--others", "--exclude-standard", "--", "*.py"],
        ["git", "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=A", "-r", "HEAD^", "HEAD", "--", "*.py"],
    )
    paths: set[str] = set()
    diagnostics: list[str] = []
    for command in commands:
        result = run_command(command)
        if result.returncode != 0:
            diagnostics.append(f"$ {' '.join(command)}\n{result.output}")
            continue
        paths.update(line.strip().replace("\\", "/") for line in result.output.splitlines() if line.strip())
    return paths, "\n".join(diagnostics)


def _is_production_python_module(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    if not parts or Path(relative_path).suffix.lower() != ".py":
        return False
    return parts[0] not in {"scripts", "tests", ".tmp", "LocalBuilds", "artifacts"}


def forbidden_symbol_step() -> StepResult:
    paths, diagnostics = _git_added_python_paths()
    if diagnostics:
        return StepResult(ok=False, output=diagnostics + "\n")
    candidates = sorted(path for path in paths if _is_production_python_module(path))
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(symbol) for symbol in FORBIDDEN_SYMBOLS) + r")\b")
    hits: list[str] = []
    for relative_path in candidates:
        path = REPO_ROOT / relative_path
        if not path.is_file():
            hits.append(f"{relative_path}: added module is not readable")
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            matches = sorted(set(pattern.findall(line)))
            if matches:
                hits.append(f"{relative_path}:{line_number}: {', '.join(matches)}: {line}")
    lines = ["Added production Python modules checked:"]
    lines.extend(f"  {path}" for path in candidates)
    if not candidates:
        lines.append("  (none)")
    if hits:
        lines.append("Forbidden symbol hits:")
        lines.extend(f"  {hit}" for hit in hits)
    else:
        lines.append("Forbidden symbol hits: (none)")
    return StepResult(ok=not hits, output="\n".join(lines) + "\n")


def _find_unity_editor() -> Path | None:
    configured = os.environ.get("VRCFORGE_RELEASE_GATE_UNITY_EDITOR", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            Path(r"E:\unity\Unity 2022.3.22f1\Editor\Unity.exe"),
            Path(r"C:\Program Files\Unity\Hub\Editor\2022.3.22f1\Editor\Unity.exe"),
        ]
    )
    hub_root = Path(r"C:\Program Files\Unity\Hub\Editor")
    if hub_root.is_dir():
        candidates.extend(sorted(hub_root.glob("*/Editor/Unity.exe"), reverse=True))
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def _find_unity_template_project() -> Path | None:
    configured = os.environ.get("VRCFORGE_RELEASE_GATE_UNITY_TEMPLATE_PROJECT", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            REPO_ROOT / "artifacts" / "acceptance-harness" / "disposable-projects" / "live80-20260824-dogfood-clone",
            REPO_ROOT / "artifacts" / "acceptance-harness" / "disposable-projects" / "live80-20260824-seed",
        ]
    )
    return next(
        (
            candidate.resolve()
            for candidate in candidates
            if (candidate / "Packages" / "manifest.json").is_file()
            and (candidate / "ProjectSettings" / "ProjectVersion.txt").is_file()
        ),
        None,
    )


def unity_compile_step() -> StepResult:
    editor = _find_unity_editor()
    template = _find_unity_template_project()
    if editor is None:
        return StepResult(
            ok=False,
            output="Unity editor not found. Set VRCFORGE_RELEASE_GATE_UNITY_EDITOR to Unity.exe.\n",
        )
    if template is None:
        return StepResult(
            ok=False,
            output=(
                "Disposable Unity template project not found. Set "
                "VRCFORGE_RELEASE_GATE_UNITY_TEMPLATE_PROJECT to a project containing Packages and ProjectSettings.\n"
            ),
        )
    temp_root = Path(tempfile.mkdtemp(prefix="vrcforge-release-gate-unity-"))
    project = temp_root / "project"
    log_path = temp_root / "unity.log"
    output = f"Unity editor: {editor}\nTemplate project: {template}\nIsolated project: {project}\n"
    ok = False
    try:
        shutil.copytree(template / "Packages", project / "Packages")
        shutil.copytree(template / "ProjectSettings", project / "ProjectSettings")
        (project / "Assets").mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO_ROOT / "Assets" / "VRCForge", project / "Assets" / "VRCForge")
        result = run_command(
            [
                str(editor),
                "-batchmode",
                "-nographics",
                "-quit",
                "-projectPath",
                str(project),
                "-logFile",
                str(log_path),
            ],
            cwd=temp_root,
            timeout=1800,
        )
        log_output = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        output += result.output
        if log_output and log_output not in output:
            output += "\n--- Unity log ---\n" + log_output
        compile_error = re.search(r"(?:error CS\d+|Compilation failed|Scripts have compiler errors)", output, re.IGNORECASE)
        clean_exit = "Exiting batchmode successfully now!" in output
        ok = result.returncode == 0 and compile_error is None and clean_exit
        if not clean_exit:
            output += "\nUnity success marker was not observed.\n"
    except (OSError, shutil.Error) as exc:
        output += f"\nUnity isolation setup failed: {type(exc).__name__}: {exc}\n"
    cleanup_error = cleanup_directory(temp_root)
    if cleanup_error:
        output += "\n" + cleanup_error
        ok = False
    return StepResult(ok=ok, output=output)


def _copy_source_tree(destination: Path) -> None:
    isolated_name = destination.name

    def ignore(_directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in SOURCE_COPY_EXCLUDES}
        ignored.update(name for name in names if name.startswith(".release-gate-"))
        ignored.add(isolated_name)
        return ignored

    shutil.copytree(REPO_ROOT, destination, ignore=ignore)


def tauri_build_step() -> StepResult:
    temp_root = Path(tempfile.mkdtemp(prefix=".release-gate-tauri-", dir=REPO_ROOT))
    source = temp_root / "source"
    output = f"Isolated Tauri source: {source}\n"
    ok = False
    try:
        _copy_source_tree(source)
        executable = "npm.cmd" if os.name == "nt" else "npm"
        result = run_command(
            [executable, "run", "tauri:build"],
            cwd=source,
            env={
                "CARGO_TARGET_DIR": str(temp_root / "cargo-target"),
                "PATH": str(REPO_ROOT / "node_modules" / ".bin")
                + os.pathsep
                + os.environ.get("PATH", ""),
            },
            timeout=3600,
        )
        output += result.output
        ok = result.returncode == 0
    except (OSError, shutil.Error) as exc:
        output += f"\nTauri isolation setup failed: {type(exc).__name__}: {exc}\n"
    cleanup_error = cleanup_directory(temp_root)
    if cleanup_error:
        output += "\n" + cleanup_error
        ok = False
    return StepResult(ok=ok, output=output)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _smoke_environment(temp_root: Path) -> dict[str, str]:
    return {
        "LOCALAPPDATA": str(temp_root / "localappdata"),
        "VRCFORGE_APP_DIR": str(REPO_ROOT),
        "VRCFORGE_USER_DATA_DIR": str(temp_root / "user-data"),
        "VRCFORGE_CONFIG_DIR": str(temp_root / "config"),
        "VRCFORGE_CONFIG_PATH": str(temp_root / "config" / "config.json"),
        "VRCFORGE_LOG_DIR": str(temp_root / "logs"),
        "VRCFORGE_ARTIFACTS_DIR": str(temp_root / "artifacts"),
        "VRCFORGE_SETTINGS_PATH": str(temp_root / "config" / "settings.json"),
        "VRCFORGE_APP_SESSION_TOKEN": "release-gate-isolated-session-token",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def backend_smoke_step() -> StepResult:
    temp_root = Path(tempfile.mkdtemp(prefix="vrcforge-release-gate-smoke-"))
    stop_path = temp_root / "stop"
    route_path = temp_root / "routes.json"
    log_path = temp_root / "backend.log"
    port = _free_loopback_port()
    child_code = "\n".join(
        [
            "import json, pathlib, threading, time, uvicorn",
            "import dashboard_server",
            f"stop_path = pathlib.Path({str(stop_path)!r})",
            f"route_path = pathlib.Path({str(route_path)!r})",
            "route_path.write_text(json.dumps({'count': len(dashboard_server.app.routes)}), encoding='utf-8')",
            f"config = uvicorn.Config(dashboard_server.app, host='127.0.0.1', port={port}, log_level='info', access_log=False)",
            "server = uvicorn.Server(config)",
            "def watch_stop():",
            "    while not stop_path.exists():",
            "        time.sleep(0.05)",
            "    server.should_exit = True",
            "threading.Thread(target=watch_stop, daemon=True).start()",
            "server.run()",
        ]
    )
    process_env = os.environ.copy()
    blocked_names = {name.casefold() for name in RUNTIME_ENVIRONMENT_NAMES}
    for existing_key in tuple(process_env):
        if existing_key.casefold() in blocked_names:
            process_env.pop(existing_key)
    process_env.update(_smoke_environment(temp_root))
    log_handle = log_path.open("w", encoding="utf-8", errors="replace")
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", child_code],
            cwd=REPO_ROOT,
            env=process_env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        log_handle.close()
        cleanup_error = cleanup_directory(temp_root)
        output = f"Backend process failed to start: {type(exc).__name__}: {exc}\n"
        if cleanup_error:
            output += cleanup_error
        return StepResult(ok=False, output=output)
    output = f"Isolated backend PID: {process.pid}\nLoopback port: {port}\nIsolation root: {temp_root}\n"
    ok = False
    health_payload: object | None = None
    deadline = time.monotonic() + 60.0
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.0) as response:
                    health_payload = json.loads(response.read().decode("utf-8"))
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                time.sleep(0.1)
        if health_payload is None:
            output += "Health endpoint did not return a JSON response within 60 seconds.\n"
        elif not route_path.is_file():
            output += f"Backend did not record its route count at {route_path}.\n"
        else:
            expected_contract = json.loads(COMPOSITION_CONTRACT_PATH.read_text(encoding="utf-8"))
            expected_count = int(expected_contract["routes"]["count"])
            actual_count = int(json.loads(route_path.read_text(encoding="utf-8"))["count"])
            health_schema = health_payload.get("schema") if isinstance(health_payload, dict) else None
            output += f"Health schema: {health_schema}\nExpected route count: {expected_count}\nActual route count: {actual_count}\n"
            ok = actual_count == expected_count and bool(health_schema)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        output += f"Backend smoke evaluation failed: {type(exc).__name__}: {exc}\n"
        ok = False
    finally:
        try:
            stop_path.touch(exist_ok=True)
        except OSError as exc:
            output += f"Backend stop sentinel failed: {type(exc).__name__}: {exc}\n"
            ok = False
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
            output += "Backend did not stop cleanly within 30 seconds and was killed.\n"
            ok = False
        log_handle.close()
        child_output = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        output += "\n--- backend output ---\n" + (child_output or "")
        if process.returncode != 0:
            output += f"\nBackend exit code: {process.returncode}\n"
            ok = False
    cleanup_error = cleanup_directory(temp_root)
    if cleanup_error:
        output += "\n" + cleanup_error
        ok = False
    return StepResult(ok=ok, output=output)


def tier_one_steps() -> tuple[Step, ...]:
    return (
        ("complete pytest and exact known-failure set", full_pytest_step),
        ("six characterization gates", characterization_step),
        ("TypeScript tsc --noEmit", tsc_step),
        ("Tauri cargo check (isolated target)", cargo_check_step),
        ("new-module forbidden-symbol scan", forbidden_symbol_step),
    )


def tier_two_steps() -> tuple[Step, ...]:
    return (
        *tier_one_steps(),
        ("Unity full compile (isolated project)", unity_compile_step),
        ("Tauri full build (isolated source and target)", tauri_build_step),
        ("isolated backend health and route-count smoke", backend_smoke_step),
    )


def execute_steps(steps: Iterable[Step]) -> int:
    for name, action in steps:
        print(f"\n=== START: {name} ===", flush=True)
        started = time.perf_counter()
        try:
            result = action()
        except Exception:  # gate must fail closed while preserving the exception text
            result = StepResult(ok=False, output=traceback.format_exc())
        elapsed = time.perf_counter() - started
        if result.output:
            print(result.output, end="" if result.output.endswith("\n") else "\n", flush=True)
        status = "PASS" if result.ok else "FAIL"
        print(f"=== {status}: {name} ({elapsed:.3f}s) ===", flush=True)
        if not result.ok:
            print(f"Release gate stopped at failed item: {name}", flush=True)
            return 1
    print("\nRelease gate passed.", flush=True)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VRCForge release readiness gates.")
    parser.add_argument("--tier", required=True, type=int, choices=(1, 2))
    return parser.parse_args(argv)


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    configure_console_encoding()
    args = parse_args(argv)
    return execute_steps(tier_one_steps() if args.tier == 1 else tier_two_steps())


if __name__ == "__main__":
    raise SystemExit(main())
