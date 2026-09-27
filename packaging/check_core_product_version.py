from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path


PYTHON_CONTRACT = Path("unity_mcp_tool_contract.py")
CSHARP_CONTRACT = Path(
    "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs"
)
_CSHARP_PRODUCT_VERSION = re.compile(
    r'\bconst\s+string\s+ProductVersion\s*=\s*"([^"]+)"\s*;'
)


class VersionConsistencyError(ValueError):
    """Raised when release and Core product identities do not match."""


def _python_product_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PRODUCT_VERSION":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    raise VersionConsistencyError(f"Python PRODUCT_VERSION is missing: {path}")


def _csharp_product_version(path: Path) -> str:
    match = _CSHARP_PRODUCT_VERSION.search(path.read_text(encoding="utf-8-sig"))
    if match is None:
        raise VersionConsistencyError(f"C# Core ProductVersion is missing: {path}")
    return match.group(1)


def validate_core_product_version(repo_root: Path, requested_version: str) -> dict[str, str]:
    requested = str(requested_version).strip()
    if not requested:
        raise VersionConsistencyError("Requested release VERSION is empty.")

    python_version = _python_product_version(repo_root / PYTHON_CONTRACT)
    csharp_version = _csharp_product_version(repo_root / CSHARP_CONTRACT)
    mismatches = []
    if python_version != requested:
        mismatches.append(
            f"Python PRODUCT_VERSION={python_version!r} != requested VERSION={requested!r}"
        )
    if csharp_version != requested:
        mismatches.append(
            f"C# Core ProductVersion={csharp_version!r} != requested VERSION={requested!r}"
        )
    if mismatches:
        raise VersionConsistencyError("Core product version consistency failed: " + "; ".join(mismatches))

    return {
        "requestedVersion": requested,
        "pythonProductVersion": python_version,
        "csharpProductVersion": csharp_version,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Require Python and Unity Core product versions to match a release VERSION."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        result = validate_core_product_version(Path(args.repo_root).resolve(), args.version)
    except (OSError, SyntaxError, VersionConsistencyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "Core product version consistency passed: "
        f"VERSION={result['requestedVersion']} "
        f"Python={result['pythonProductVersion']} "
        f"CSharp={result['csharpProductVersion']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
