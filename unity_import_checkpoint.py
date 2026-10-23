"""Derive bounded asset rollback footprints from approved, sealed imports."""
from pathlib import Path, PurePosixPath
from contextlib import ExitStack
import hashlib
import tarfile
import tempfile
import zipfile

from prepared_archive_imports import _entry_manifest, _opened_source_matches
from prepared_file_imports import verify_directory, verify_regular_file
from prepared_unity_execution import build_prepared_execution_plan, prepared_evidence
from outfit_import_planner import UNITY_IMPORT_CODE_PAYLOAD_SUFFIXES


def prepare_import_asset_checkpoint(project_root: Path, arguments: dict, *, verify_project, prepare=None) -> dict:
    """Inspect every imported destination; never accept a caller footprint."""
    def reject(message):
        return {"ok": False, "blocking": True, "code": "checkpoint_scope_unresolved",
                "error": message, "mutationStarted": False, "saved": False}

    try:
        evidence = prepared_evidence(arguments)
        calls = build_prepared_execution_plan(arguments)
        project = verify_project(evidence["projectIdentity"])
        if Path(project).resolve() != project_root.resolve():
            raise ValueError("The sealed import targets another project.")
        plan = evidence.get("plan") or {}
        if evidence.get("kind") != plan.get("kind"):
            raise ValueError("The sealed import kind differs from its plan.")
        destinations = set()
        directories = set()
        code_bearing_paths = []

        def canonical(name):
            if (not isinstance(name, str) or not name.startswith("Assets/")
                    or "\\" in name or ":" in name or "\x00" in name
                    or PurePosixPath(name).as_posix() != name
                    or any(x in {"", ".", ".."} for x in name.split("/"))):
                raise ValueError("Import rollback requires canonical Assets/ destinations.")
            target = project_root / name
            # Check every existing ancestor before resolving, including junctions.
            cursor = target
            while cursor != project_root:
                if cursor.exists() or cursor.is_symlink():
                    attributes = int(getattr(cursor.lstat(), "st_file_attributes", 0))
                    if cursor.is_symlink() or attributes & 0x400:
                        raise ValueError("Import rollback destination contains a linked path.")
                cursor = cursor.parent
            target.resolve().relative_to(project_root.resolve())
            return target

        def include(name, *, folder=False):
            target = canonical(name)
            if folder:
                if target.exists() and not target.is_dir():
                    raise ValueError("An import folder conflicts with an existing file.")
                if not target.exists():
                    directories.add(name)
            else:
                if target.exists() and not target.is_file():
                    raise ValueError("An imported file conflicts with an existing directory.")
                destinations.add(name)
            destinations.add(name + ".meta" if not name.endswith(".meta") else name)
            parent = PurePosixPath(name).parent
            while parent.as_posix() != "Assets":
                parent_name = parent.as_posix()
                parent_path = canonical(parent_name)
                if not parent_path.exists():
                    directories.add(parent_name)
                    destinations.add(parent_name + ".meta")
                elif not parent_path.is_dir():
                    raise ValueError("An import parent conflicts with an existing file.")
                parent = parent.parent

        refresh = ("vrc_refresh_asset_database", {
            "projectPath": evidence["projectIdentity"]["projectPath"],
            "resolvePackages": False, "packageResolveTimeoutSeconds": 120,
        })
        if evidence["kind"] == "loose_prefab_copy":
            if calls != [refresh]:
                raise ValueError("The loose import's sealed Core calls changed.")
            loose = evidence["loosePlan"]
            verify_directory(loose["sourceRoot"], label="Import source")
            if verify_directory(loose["project"], label="Unity project").resolve() != project_root.resolve():
                raise ValueError("The loose import project changed.")
            verify_directory(loose["assets"], label="Unity Assets")
            for item in loose["files"]:
                verify_regular_file(item["sourceIdentity"], item["sourceSha256"], label="Import source")
                target = item["target"]
                target_relative_path = target["targetRelativePath"]
                if PurePosixPath(target_relative_path).suffix.casefold() in UNITY_IMPORT_CODE_PAYLOAD_SUFFIXES:
                    code_bearing_paths.append(target_relative_path)
                for identity in target.get("parentIdentities", []):
                    verify_directory(identity, label="Import parent")
                for name in [target_relative_path, *target.get("absentParentRelativePaths", [])]:
                    candidate = canonical(name)
                    if candidate.exists():
                        raise ValueError("An approved absent import destination appeared.")
                include(target_relative_path)
        elif evidence["kind"] in {"unitypackage_import", "unitypackage_import_sequence"}:
            queue = evidence["queue"]
            if not queue or len(calls) != len(queue) + 1 or calls[-1] != refresh:
                raise ValueError("The sealed package queue is incomplete.")
            for index, item in enumerate(queue):
                identity = item["identity"]
                package = Path(identity["path"])
                tool, request = calls[index]
                if (tool != "vrc_import_unitypackage" or request.get("unityPackagePath") != str(package)
                        or request.get("expectedSha256") != identity["sha256"]
                        or request.get("expectedSize") != identity["size"]
                        or request.get("projectPath") != refresh[1]["projectPath"]):
                    raise ValueError("A sealed package call differs from its approved source.")
                seen = set()
                with ExitStack() as resources:
                    materialization_index = item.get("materializationIndex")
                    if materialization_index is None:
                        package = verify_regular_file(
                            {key: value for key, value in identity.items() if key != "sha256"},
                            identity["sha256"], label="Import package",
                        )
                        archive = resources.enter_context(tarfile.open(package, "r:*"))
                    else:
                        facts_list = evidence.get("materializations")
                        if (type(materialization_index) is not int or not isinstance(facts_list, list)
                                or not 0 <= materialization_index < len(facts_list)):
                            raise ValueError("The nested package materialization index is invalid.")
                        facts = facts_list[materialization_index]
                        if facts.get("schema") != "vrcforge.prepared-zip-materialization.v1":
                            raise ValueError("The nested package materialization facts are invalid.")
                        selected = facts.get("selected")
                        if not isinstance(selected, list) or len(selected) != 1:
                            raise ValueError("The nested package must bind exactly one ZIP member.")
                        selected = selected[0]
                        parent = verify_directory(facts["tempParent"], label="Materialization temp parent")
                        target_name = selected["targetName"]
                        if Path(target_name).name != target_name or target_name in {"", ".", ".."}:
                            raise ValueError("The nested package target name is invalid.")
                        expected_identity = {"path": str(parent / target_name),
                                             "sha256": selected["sha256"], "size": selected["size"]}
                        if identity != expected_identity or package.exists() or package.is_symlink():
                            raise ValueError("The nested package target drifted from its sealed ZIP member.")
                        source = verify_regular_file(facts["sourceIdentity"], facts["sourceSha256"], label="Archive source")
                        handle = resources.enter_context(source.open("rb"))
                        _opened_source_matches(handle, facts["sourceIdentity"], facts["sourceSha256"])
                        container = resources.enter_context(zipfile.ZipFile(handle))
                        # The producer seals only the selected member(s). Rebuild that
                        # same bounded manifest at checkpoint time; comparing against
                        # every companion file in the outer ZIP rejects valid
                        # selected-only imports before the member hash is checked.
                        manifest = _entry_manifest(
                            container,
                            selected_paths={str(selected["path"])},
                            max_uncompressed_bytes=None,
                        )
                        if manifest != facts.get("manifest") or not any(
                            selected == {**entry, "targetName": target_name} for entry in manifest
                        ):
                            raise ValueError("The nested package ZIP member drifted from its sealed manifest.")
                        # The executor owns the future materialization. Inspection uses only an
                        # auto-closed spool in the already-owned temp directory, never that path.
                        spool = resources.enter_context(tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, dir=parent))
                        digest = hashlib.sha256()
                        size = 0
                        with container.open(selected["zipPath"]) as member_source:
                            while chunk := member_source.read(1024 * 1024):
                                size += len(chunk)
                                if size > selected["size"]:
                                    raise ValueError("The nested package exceeded its sealed size.")
                                spool.write(chunk)
                                digest.update(chunk)
                        if size != selected["size"] or digest.hexdigest() != selected["sha256"]:
                            raise ValueError("The nested package content drifted from its sealed identity.")
                        verify_regular_file(facts["sourceIdentity"], facts["sourceSha256"], label="Archive source")
                        spool.seek(0)
                        archive = resources.enter_context(tarfile.open(fileobj=spool, mode="r:*"))
                    members = archive.getmembers()
                    if len(members) > 200000:
                        raise ValueError("The package exceeds the bounded footprint scan.")
                    if len({member.name.casefold() for member in members}) != len(members):
                        raise ValueError("The package contains duplicate archive members.")
                    for member in members:
                        if not member.isfile() or not member.name.endswith("/pathname"):
                            continue
                        with archive.extractfile(member) as source:
                            name = source.read(32769).decode("utf8").strip()
                        canonical(name)
                        if name.casefold() in seen:
                            raise ValueError("The package contains duplicate asset paths.")
                        seen.add(name.casefold())
                        # Importing code can run arbitrary asset callbacks; a
                        # pathname list cannot prove their write footprint. Keep
                        # scanning the sealed archive for identity/path checks,
                        # then use the established full-project snapshot below.
                        if PurePosixPath(name).suffix.casefold() in UNITY_IMPORT_CODE_PAYLOAD_SUFFIXES:
                            code_bearing_paths.append(name)
                        prefix = member.name.rsplit("/", 1)[0]
                        meta = archive.getmember(prefix + "/asset.meta")
                        with archive.extractfile(meta) as source:
                            folder = b"folderAsset: yes" in source.read(32768)
                        if not folder:
                            payload = archive.getmember(prefix + "/asset")
                            if not payload.isfile():
                                raise ValueError("A package file has no regular payload.")
                        include(name, folder=folder)
                if not seen:
                    raise ValueError("The package has no complete asset footprint.")
        else:
            raise ValueError("This import kind has no proven asset footprint.")
        if not destinations or len(destinations) > 4096:
            raise ValueError("The import has no bounded file footprint.")
        if code_bearing_paths:
            risk_facts = plan.get("riskFacts") if isinstance(plan, dict) else None
            sealed_code_scope = (
                isinstance(risk_facts, dict)
                and risk_facts.get("codeBearingPackage") is True
                and risk_facts.get("fullProjectCheckpointRequired") is True
                and risk_facts.get("checkpointScope") == "full_project"
                and risk_facts.get("recommendedRiskLevel") == "high"
                and risk_facts.get("mediumEligible") is False
                and isinstance(risk_facts.get("reasonCodes"), list)
                and "code_bearing_package_requires_full_project_checkpoint"
                in (risk_facts.get("reasonCodes") or [])
            )
            if not sealed_code_scope:
                raise ValueError(
                    "The sealed import plan predates code-bearing full-project scope classification; re-plan and approve it again."
                )
            inspected = prepare(project_root, checkpoint_files=None) if prepare else {"ok": True}
            if inspected.get("ok") is not True:
                return inspected
            return {
                **inspected,
                "ok": True,
                "checkpointScope": "full_project",
                "codeBearingPaths": sorted(set(code_bearing_paths)),
                "archiveScopeReason": "code_bearing_package_full_project",
            }
        existing, absent = [], []
        for name in sorted(destinations):
            target = canonical(name)
            if target.is_file():
                existing.append(name)
            elif not target.exists():
                absent.append(name)
            else:
                raise ValueError("The import footprint contains a non-file destination.")
        inspected = prepare(project_root, checkpoint_files=sorted(destinations)) if prepare else {"ok": True}
        if inspected.get("ok") is not True:
            return inspected
        return {**inspected, "ok": True, "assetFootprint": {
            "schema": "vrcforge.asset_checkpoint_footprint.v1",
            "archiveFiles": existing, "archiveAbsentFiles": absent,
            "archiveAbsentDirectories": sorted(directories),
        }, "archiveScopeReason": "sealed_import_complete_asset_footprint"}
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, tarfile.TarError) as exc:
        return reject(str(exc))
