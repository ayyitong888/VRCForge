"""Build the ten public Avatar Skill archives for the frozen backend.

Only tracked source packages are read. This does not install, project, enable,
or write to a user's Skill library.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skill_packages import SkillPackageService


SOURCES = (
    ("artifacts/skills/vrcforge-avatar-accessory-switch", "release"),
    ("artifacts/skills/vrcforge-avatar-animation", "release"),
    ("artifacts/skills/vrcforge-avatar-audit", "release"),
    ("artifacts/skills/vrcforge-avatar-breast-physics-audit", "release"),
    ("artifacts/skills/vrcforge-avatar-expression-menu", "release"),
    ("artifacts/skills/vrcforge-avatar-hairstyle", "release"),
    ("artifacts/skills/vrcforge-avatar-wardrobe", "release"),
    ("examples/skill-packages/vrcforge-avatar-head-transplant", "release"),
    ("examples/skill-packages/vrcforge-avatar-part-transplant", "release"),
    ("artifacts/skills/vrcforge-avatar-slime", "dev"),
)


def build(output: Path, signing_key: Path, app_version: str) -> dict:
    if not signing_key.is_file():
        raise ValueError("The existing Avatar Skill signing key is required to preserve signed-package upgrades. Pass --signing-key or VRCFORGE_SKILL_SIGNING_KEY_PATH; no new key is generated.")
    output.mkdir(parents=True, exist_ok=True)
    packages = []
    with tempfile.TemporaryDirectory(prefix="vrcforge-bundled-skill-export-") as temporary:
        service = SkillPackageService(Path(temporary) / "store", vrcforge_version=app_version)
        for relative, mode in SOURCES:
            source = ROOT / relative
            manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            filename = source.name + "-" + manifest["version"] + ".vsk"
            if mode == "release":
                result = service.export_release(source, output / filename, signing_key)
            else:
                result = service.export_dev(source, output / filename)
            packages.append({"id": result.manifest["id"], "version": result.manifest["version"], "file": result.package_path.name})
    index = {"packages": packages}
    (output / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, default=os.environ.get("VRCFORGE_SKILL_SIGNING_KEY_PATH"))
    parser.add_argument("--version", default=(ROOT / "VERSION").read_text().strip())
    args = parser.parse_args()
    if args.signing_key is None:
        parser.error("--signing-key or VRCFORGE_SKILL_SIGNING_KEY_PATH must point to the existing Avatar Skill signer")
    index = build(args.output.resolve(), args.signing_key, args.version)
    print(json.dumps({"packages": len(index["packages"]), "index": str(args.output / "index.json")}))


if __name__ == "__main__":
    main()
