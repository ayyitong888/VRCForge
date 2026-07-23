from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from diagnostic_privacy import redact_public_evidence  # noqa: E402
from primitive_basis_matrix import (  # noqa: E402
    EVIDENCE_SCHEMA,
    MatrixContractError,
    VerificationContext,
    build_report,
    load_fixture_set,
)


def main() -> int:
    args = parse_args()
    try:
        fixtures = load_fixture_set(
            REPO_ROOT / "tests" / "fixtures" / "primitive_basis",
            repository_root=args.repository_root,
        )
        evidence = load_evidence(args.evidence)
        verification = build_verification_context(args)
        report = build_report(fixtures, evidence, verification=verification)
        output_path = write_report(report, args.artifacts_dir)
    except MatrixContractError as exc:
        detail = str(exc)
        if redact_public_evidence(detail) != detail:
            detail = "contract rejected"
        print(
            json.dumps(
                {"ok": False, "error": "primitive basis contract rejected", "detail": detail},
                ensure_ascii=True,
                indent=2,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "ok": report["ok"],
                "status": report["summary"]["status"],
                "reportName": output_path.name,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the fixed VRCForge primitive-basis evidence matrix."
    )
    parser.add_argument("--repository-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--evidence",
        default="",
        help="Evidence JSON. Omit to emit the explicit all-blocked baseline.",
    )
    parser.add_argument("--receipts-root", default="")
    parser.add_argument("--release-manifest", default="")
    parser.add_argument("--executable", default="")
    parser.add_argument(
        "--artifacts-dir",
        default=str(REPO_ROOT / "artifacts" / "primitive-basis-matrix"),
    )
    return parser.parse_args()


def build_verification_context(args: argparse.Namespace) -> VerificationContext | None:
    values = (
        str(args.receipts_root or ""),
        str(args.release_manifest or ""),
        str(args.executable or ""),
    )
    if not any(values):
        return None
    if not all(values):
        raise MatrixContractError("runtime verifier arguments must be complete")
    return VerificationContext(
        repository_root=Path(args.repository_root),
        receipts_root=Path(args.receipts_root),
        release_manifest_path=Path(args.release_manifest),
        executable_path=Path(args.executable),
    )


def load_evidence(path_value: str) -> dict[str, Any]:
    if not path_value:
        return {
            "schema": EVIDENCE_SCHEMA,
            "runId": "primitive-basis-baseline",
            "rows": [],
        }
    path = Path(path_value)
    try:
        with path.open("rb") as handle:
            payload_bytes = handle.read(MAX_EVIDENCE_BYTES + 1)
        if len(payload_bytes) > MAX_EVIDENCE_BYTES:
            raise MatrixContractError("evidence JSON is too large")
        payload = json.loads(
            payload_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except MatrixContractError:
        raise
    except (OSError, UnicodeError, RecursionError, ValueError) as exc:
        raise MatrixContractError("evidence JSON is unavailable") from exc
    if not isinstance(payload, dict):
        raise MatrixContractError("evidence must be an object")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MatrixContractError("duplicate JSON field")
        result[key] = value
    return result


def write_report(report: dict[str, Any], artifacts_dir: str) -> Path:
    output_dir = Path(artifacts_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MatrixContractError("matrix report directory is unavailable") from exc
    for _ in range(8):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        suffix = secrets.token_hex(4)
        output_path = output_dir / f"primitive-basis-matrix-{timestamp}-{suffix}.json"
        try:
            with output_path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
            return output_path
        except FileExistsError:
            continue
        except OSError as exc:
            raise MatrixContractError("matrix report could not be written") from exc
    raise MatrixContractError("matrix report name collision")


if __name__ == "__main__":
    raise SystemExit(main())
