from __future__ import annotations

import json
import shutil
from pathlib import Path


MARKER_NAME = ".vrcforge-data-root.json"
MARKER_SCHEMA = 1

# Known legacy runtime-data entries under the old user data root, in
# migration order. Anything else at the old root top level is preserved.
LEGACY_RUNTIME_ITEMS = (
    "logs",
    "artifacts",
    "backups",
    "project-indexes",
    "signing",
    "project-cache.json",
)


def migrate_legacy_runtime_data(
    old_root: Path,
    new_data_dir: Path,
    *,
    dry_run: bool = False,
) -> dict:
    """One-shot move of known legacy runtime data into the new data root.

    - Known items (dirs + project-cache.json) move wholesale when the source
      exists and the target does not. native-history travels inside
      `artifacts`; no special-casing.
    - An existing target is never merged or overwritten: the item is skipped
      and reported.
    - Unknown top-level entries stay in the old root and are reported as
      preserved.
    - On success a marker file is written into the new root so later runs
      short-circuit with {"alreadyMigrated": True}.
    - dry_run computes the full report without touching the filesystem.
    """
    old_root = Path(old_root)
    new_data_dir = Path(new_data_dir)

    marker_path = new_data_dir / MARKER_NAME
    if marker_path.exists():
        return {"alreadyMigrated": True}

    report = {
        "alreadyMigrated": False,
        "dryRun": dry_run,
        "oldRoot": str(old_root),
        "newDataDir": str(new_data_dir),
        "moved": [],
        "skipped": [],
        "preserved": [],
        "markerWritten": False,
    }

    if not old_root.exists():
        return report

    known = set(LEGACY_RUNTIME_ITEMS)
    for entry in sorted(old_root.iterdir(), key=lambda item: item.name):
        if entry.name not in known:
            report["preserved"].append(entry.name)

    for name in LEGACY_RUNTIME_ITEMS:
        source = old_root / name
        if not source.exists():
            continue
        target = new_data_dir / name
        if target.exists():
            # Never merge, never overwrite existing user data.
            report["skipped"].append(name)
            continue
        report["moved"].append(name)
        if not dry_run:
            new_data_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))

    if not dry_run:
        new_data_dir.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps({"schema": MARKER_SCHEMA, "migratedFrom": str(old_root)}),
            encoding="utf-8",
        )
        report["markerWritten"] = True

    return report
