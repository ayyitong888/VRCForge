from __future__ import annotations

import argparse
import ast
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "vrcforge.one_five_seam_gate.v1"
MANIFEST_SCHEMA = "vrcforge.one_five_owner_facade_manifest.v1"
DEFAULT_MANIFEST = "packaging/one_five_owner_facade_manifest.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reject VRCForge 1.5.0+ while a declared owner/facade migration "
            "seam or an undeclared legacy proxy remains."
        )
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--full-report", action="store_true")
    return parser.parse_args(argv)


def _safe_repo_path(repo_root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ValueError(f"manifest source path must be repository-relative: {relative!r}")
    candidate = (repo_root / relative).resolve()
    if os.path.commonpath((str(repo_root), str(candidate))) != str(repo_root):
        raise ValueError(f"manifest source path escapes repository: {relative!r}")
    return candidate


def _read_manifest(repo_root: Path, manifest_path: str) -> dict[str, Any]:
    path = _safe_repo_path(repo_root, manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("owner/facade manifest must be a JSON object")
    return payload


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?", value.strip())
    if not match:
        raise ValueError(f"version must be normalized semantic version: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


class SourceTrees:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._texts: dict[str, str] = {}
        self._trees: dict[str, ast.Module | None] = {}

    def text(self, source: str) -> str:
        if source not in self._texts:
            path = _safe_repo_path(self.repo_root, source)
            self._texts[source] = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        return self._texts[source]

    def tree(self, source: str) -> ast.Module | None:
        if source not in self._trees:
            text = self.text(source)
            self._trees[source] = ast.parse(text, filename=source) if text else None
        return self._trees[source]


def _class_node(tree: ast.Module | None, class_name: str) -> ast.ClassDef | None:
    if tree is None:
        return None
    return next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name),
        None,
    )


def _scope_functions(
    tree: ast.Module | None,
    *,
    scope: str,
    class_name: str = "",
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    if tree is None:
        return []
    body: Iterable[ast.stmt]
    if scope == "module":
        body = tree.body
    elif scope == "class":
        class_node = _class_node(tree, class_name)
        body = class_node.body if class_node is not None else []
    else:
        raise ValueError(f"unsupported facade scope: {scope!r}")
    return [node for node in body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _inspect_root_symbol(trees: SourceTrees, check: dict[str, Any]) -> list[dict[str, Any]]:
    source = str(check.get("source") or "")
    scope = str(check.get("scope") or "")
    name = str(check.get("name") or "")
    class_name = str(check.get("class") or "")
    tree = trees.tree(source)
    if tree is None:
        return []
    if scope == "module":
        nodes: Iterable[ast.AST] = ast.walk(tree)
        return [
            {"source": source, "line": node.lineno, "symbol": name}
            for node in nodes
            if isinstance(node, ast.Name) and node.id == name
        ]
    if scope == "class":
        class_node = _class_node(tree, class_name)
        if class_node is None:
            return []
        return [
            {"source": source, "line": node.lineno, "symbol": f"{class_name}.{name}"}
            for node in ast.walk(class_node)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"self", "cls"}
            and node.attr == name
        ]
    raise ValueError(f"unsupported root-symbol scope: {scope!r}")


def _inspect_facades(trees: SourceTrees, check: dict[str, Any]) -> list[dict[str, Any]]:
    source = str(check.get("source") or "")
    scope = str(check.get("scope") or "")
    class_name = str(check.get("class") or "")
    names = {str(name) for name in check.get("methods", [])}
    return [
        {"source": source, "line": node.lineno, "symbol": node.name}
        for node in _scope_functions(trees.tree(source), scope=scope, class_name=class_name)
        if node.name in names
    ]


def _inspect_host_proxy(trees: SourceTrees, check: dict[str, Any]) -> list[dict[str, Any]]:
    source = str(check.get("source") or "")
    class_name = str(check.get("class") or "")
    class_node = _class_node(trees.tree(source), class_name)
    if class_node is None:
        return []
    hits: list[dict[str, Any]] = []
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__":
            hits.append({"source": source, "line": node.lineno, "symbol": f"{class_name}.__getattr__"})
    hits.extend(
        {
            "source": source,
            "line": node.lineno,
            "symbol": f"{class_name}._host",
        }
        for node in ast.walk(class_node)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"self", "cls"}
        and node.attr == "_host"
    )
    return hits


def _inspect_class_symbol(trees: SourceTrees, check: dict[str, Any]) -> list[dict[str, Any]]:
    source = str(check.get("source") or "")
    class_name = str(check.get("class") or "")
    node = _class_node(trees.tree(source), class_name)
    return [] if node is None else [{"source": source, "line": node.lineno, "symbol": class_name}]


def _inspect_markers(trees: SourceTrees, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for check in checks:
        source = str(check.get("source") or "")
        marker = str(check.get("text") or "")
        if not marker:
            continue
        for line_number, line in enumerate(trees.text(source).splitlines(), start=1):
            if marker in line:
                hits.append({"source": source, "line": line_number, "symbol": marker})
    return hits


def _single_return_impl_facades(
    trees: SourceTrees,
    *,
    source: str,
    scope: str,
    class_name: str = "",
) -> set[str]:
    candidates: set[str] = set()
    for function in _scope_functions(trees.tree(source), scope=scope, class_name=class_name):
        if len(function.body) != 1 or not isinstance(function.body[0], ast.Return):
            continue
        returned = function.body[0].value
        if returned is None:
            continue
        calls = [node for node in ast.walk(returned) if isinstance(node, ast.Call)]
        if any(isinstance(call.func, ast.Attribute) and call.func.attr.startswith("_impl") for call in calls):
            candidates.add(function.name)
    return candidates


def _discover_host_proxies(trees: SourceTrees) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(trees.repo_root.glob("*.py")):
        source = path.name
        tree = trees.tree(source)
        if tree is None:
            continue
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            has_getattr = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__"
                for node in class_node.body
            )
            has_host = any(
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in {"self", "cls"}
                and node.attr == "_host"
                for node in ast.walk(class_node)
            )
            if has_getattr and has_host:
                found.add((source, class_node.name))
    return found


def _discover_one_five_stopgaps(trees: SourceTrees) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(trees.repo_root.glob("*.py")):
        source = path.name
        lines = trees.text(source).splitlines()
        for index, line in enumerate(lines):
            if "STOPGAP" not in line:
                continue
            window = " ".join(lines[max(0, index - 2) : min(len(lines), index + 3)])
            if "1.5" in window:
                found.add((source, line.strip()))
    return found


def _validate_manifest(repo_root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != MANIFEST_SCHEMA:
        errors.append(f"manifest schema must be {MANIFEST_SCHEMA}")
    try:
        _version_tuple(str(manifest.get("enforceFromVersion") or ""))
    except ValueError as exc:
        errors.append(str(exc))
    groups = manifest.get("seamGroups")
    if not isinstance(groups, list):
        errors.append("manifest seamGroups must be a list")
        return errors
    seen_groups: set[str] = set()
    seen_facades: set[tuple[str, str, str, str]] = set()
    seen_roots: set[tuple[str, str, str, str]] = set()
    seen_proxies: set[tuple[str, str]] = set()
    seen_markers: set[tuple[str, str]] = set()
    for group in groups:
        if not isinstance(group, dict):
            errors.append("every seam group must be an object")
            continue
        group_id = str(group.get("id") or "")
        if not group_id or group_id in seen_groups:
            errors.append(f"seam group id is empty or duplicated: {group_id!r}")
        seen_groups.add(group_id)
        for collection_name in ("rootSymbols", "facades", "markers"):
            collection = group.get(collection_name, [])
            if not isinstance(collection, list):
                errors.append(f"{group_id}.{collection_name} must be a list")
        for root in group.get("rootSymbols", []):
            if not isinstance(root, dict):
                errors.append(f"{group_id}.rootSymbols contains a non-object")
                continue
            source = str(root.get("source") or "")
            scope = str(root.get("scope") or "")
            class_name = str(root.get("class") or "")
            name = str(root.get("name") or "")
            try:
                _safe_repo_path(repo_root, source)
            except ValueError as exc:
                errors.append(str(exc))
            key = (source, scope, class_name, name)
            if (
                scope not in {"module", "class"}
                or (scope == "class" and not class_name)
                or not name
                or key in seen_roots
            ):
                errors.append(f"invalid or duplicate root symbol: {key}")
            seen_roots.add(key)
        for facade in group.get("facades", []):
            if not isinstance(facade, dict):
                errors.append(f"{group_id}.facades contains a non-object")
                continue
            source = str(facade.get("source") or "")
            scope = str(facade.get("scope") or "")
            class_name = str(facade.get("class") or "")
            methods = facade.get("methods")
            try:
                _safe_repo_path(repo_root, source)
            except ValueError as exc:
                errors.append(str(exc))
            if (
                scope not in {"module", "class"}
                or (scope == "class" and not class_name)
                or not isinstance(methods, list)
                or not methods
            ):
                errors.append(f"invalid facade declaration in {group_id}: {source}:{scope}:{class_name}")
                continue
            for method in methods:
                key = (source, scope, class_name, str(method))
                if not method or key in seen_facades:
                    errors.append(f"empty or duplicate facade method: {key}")
                seen_facades.add(key)
        proxy = group.get("hostProxy")
        if proxy is not None:
            if not isinstance(proxy, dict):
                errors.append(f"{group_id}.hostProxy must be an object")
            else:
                source = str(proxy.get("source") or "")
                class_name = str(proxy.get("class") or "")
                try:
                    _safe_repo_path(repo_root, source)
                except ValueError as exc:
                    errors.append(str(exc))
                key = (source, class_name)
                if not class_name or key in seen_proxies:
                    errors.append(f"invalid or duplicate host proxy: {key}")
                seen_proxies.add(key)
        class_symbol = group.get("classSymbol")
        if class_symbol is not None:
            if not isinstance(class_symbol, dict):
                errors.append(f"{group_id}.classSymbol must be an object")
            else:
                try:
                    _safe_repo_path(repo_root, str(class_symbol.get("source") or ""))
                except ValueError as exc:
                    errors.append(str(exc))
                if not str(class_symbol.get("class") or ""):
                    errors.append(f"{group_id}.classSymbol class is empty")
        for marker in group.get("markers", []):
            if not isinstance(marker, dict):
                errors.append(f"{group_id}.markers contains a non-object")
                continue
            source = str(marker.get("source") or "")
            text = str(marker.get("text") or "")
            try:
                _safe_repo_path(repo_root, source)
            except ValueError as exc:
                errors.append(str(exc))
            key = (source, text)
            if not text or key in seen_markers:
                errors.append(f"empty or duplicate migration marker: {key}")
            seen_markers.add(key)
    allowlist = manifest.get("publicApiAllowlist")
    if not isinstance(allowlist, dict) or not isinstance(allowlist.get("contracts"), list):
        errors.append("publicApiAllowlist.contracts must be a list")
    else:
        for contract in allowlist["contracts"]:
            if not isinstance(contract, dict) or not str(contract.get("id") or ""):
                errors.append("every public API allowlist contract needs an id")
                continue
            for guard in contract.get("guardPaths", []):
                try:
                    guard_path = _safe_repo_path(repo_root, str(guard))
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                if not guard_path.is_file():
                    errors.append(f"public API contract guard is missing: {guard}")
    return errors


def inspect_tree(repo_root: Path, manifest: dict[str, Any], version: str) -> dict[str, Any]:
    root = repo_root.resolve()
    manifest_errors = _validate_manifest(root, manifest)
    try:
        enforced = _version_tuple(version) >= _version_tuple(str(manifest.get("enforceFromVersion") or ""))
    except ValueError as exc:
        manifest_errors.append(str(exc))
        enforced = True
    trees = SourceTrees(root)
    checks: list[dict[str, Any]] = []
    declared_module_facades: set[str] = set()
    declared_gateway_facades: set[str] = set()
    declared_host_proxies: set[tuple[str, str]] = set()
    declared_markers: set[tuple[str, str]] = set()

    for group in manifest.get("seamGroups", []) if isinstance(manifest.get("seamGroups"), list) else []:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("id") or "")
        for root_symbol in group.get("rootSymbols", []):
            if not isinstance(root_symbol, dict):
                continue
            hits = _inspect_root_symbol(trees, root_symbol)
            checks.append({"group": group_id, "kind": "root_symbol", "declaration": root_symbol, "hits": hits})
        for facade in group.get("facades", []):
            if not isinstance(facade, dict):
                continue
            source = str(facade.get("source") or "")
            scope = str(facade.get("scope") or "")
            class_name = str(facade.get("class") or "")
            methods = {str(name) for name in facade.get("methods", [])}
            if source == "dashboard_server.py" and scope == "module":
                declared_module_facades.update(methods)
            if source == "agent_gateway.py" and scope == "class" and class_name == "AgentGateway":
                declared_gateway_facades.update(methods)
            hits = _inspect_facades(trees, facade)
            checks.append({"group": group_id, "kind": "facades", "declaration": facade, "hits": hits})
        proxy = group.get("hostProxy")
        if isinstance(proxy, dict):
            declared_host_proxies.add((str(proxy.get("source") or ""), str(proxy.get("class") or "")))
            hits = _inspect_host_proxy(trees, proxy)
            checks.append({"group": group_id, "kind": "host_proxy", "declaration": proxy, "hits": hits})
        class_symbol = group.get("classSymbol")
        if isinstance(class_symbol, dict):
            hits = _inspect_class_symbol(trees, class_symbol)
            checks.append({"group": group_id, "kind": "class_symbol", "declaration": class_symbol, "hits": hits})
        markers = [marker for marker in group.get("markers", []) if isinstance(marker, dict)]
        declared_markers.update(
            (str(marker.get("source") or ""), str(marker.get("text") or "")) for marker in markers
        )
        if markers:
            checks.append(
                {"group": group_id, "kind": "markers", "declaration": markers, "hits": _inspect_markers(trees, markers)}
            )

    discovered_module_facades = _single_return_impl_facades(
        trees, source="dashboard_server.py", scope="module"
    )
    discovered_gateway_facades = _single_return_impl_facades(
        trees, source="agent_gateway.py", scope="class", class_name="AgentGateway"
    )
    discovered_host_proxies = _discover_host_proxies(trees)
    discovered_markers = _discover_one_five_stopgaps(trees)
    undeclared = {
        "dashboardImplFacades": sorted(discovered_module_facades - declared_module_facades),
        "gatewayImplFacades": sorted(discovered_gateway_facades - declared_gateway_facades),
        "hostProxies": [
            {"source": source, "class": class_name}
            for source, class_name in sorted(discovered_host_proxies - declared_host_proxies)
        ],
        "oneFiveStopgaps": [
            {"source": source, "text": text}
            for source, text in sorted(discovered_markers - declared_markers)
        ],
    }
    undeclared_count = sum(len(items) for items in undeclared.values())
    remaining_checks = [check for check in checks if check["hits"]]
    remaining_hit_count = sum(len(check["hits"]) for check in remaining_checks)
    remaining_projection = []
    for check in remaining_checks:
        hits = check["hits"]
        remaining_projection.append(
            {
                **check,
                "hitCount": len(hits),
                "hits": hits[:8],
                "truncated": len(hits) > 8,
            }
        )
    blocked = bool(manifest_errors or undeclared_count or (enforced and remaining_hit_count))
    if blocked:
        status = "blocked"
    elif enforced:
        status = "passed"
    else:
        status = "migration-allowed"
    return {
        "ok": not blocked,
        "schema": SCHEMA,
        "version": version,
        "enforceFromVersion": manifest.get("enforceFromVersion"),
        "enforced": enforced,
        "status": status,
        "manifestErrors": manifest_errors,
        "undeclared": undeclared,
        "summary": {
            "declaredCheckCount": len(checks),
            "remainingCheckCount": len(remaining_checks),
            "remainingHitCount": remaining_hit_count,
            "undeclaredCount": undeclared_count,
        },
        "remaining": remaining_projection,
        "policy": {
            "finalDeclaredMigrationHits": 0,
            "finalUndeclaredMigrationSeams": 0,
            "migrationFacadeNamesArePublicApi": False,
        },
    }


def _run_args(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    manifest = _read_manifest(repo_root, args.manifest)
    return inspect_tree(repo_root, manifest, str(args.version))


def run(argv: list[str] | None = None) -> dict[str, Any]:
    return _run_args(parse_args(argv))


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = _run_args(args)
    except (OSError, ValueError, json.JSONDecodeError, SyntaxError) as exc:
        args = argparse.Namespace(full_report=False)
        report = {
            "ok": False,
            "schema": SCHEMA,
            "status": "blocked",
            "error": str(exc),
        }
    output = report
    if not args.full_report and isinstance(report.get("remaining"), list):
        output = {
            **report,
            "remaining": [
                {
                    "group": item.get("group"),
                    "kind": item.get("kind"),
                    "hitCount": item.get("hitCount"),
                }
                for item in report["remaining"]
            ],
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
