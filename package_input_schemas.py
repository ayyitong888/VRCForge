"""Canonical model-facing schemas for supervised VPM package workflows."""
from __future__ import annotations

from typing import Any


PACKAGE_INSTALL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "anyOf": [{"required": ["packageId"]}, {"required": ["package_id"]}],
    "properties": {
        "projectPath": {"type": "string", "description": "Absolute Unity project path."},
        "packageId": {"type": "string", "description": "VPM package identifier."},
        "package_id": {"type": "string", "description": "Existing alias of packageId; prefer packageId for new calls."},
        "repository": {"type": "string"},
        "preferredManager": {"type": "string", "enum": ["", "vrc-get", "vcc", "alcom"]},
        "allowAgentManagedDownload": {"type": "boolean"},
        "includePrerelease": {"type": "boolean"},
        "packageVersion": {"type": "string", "description": "Optional exact semver to install or upgrade to."},
        "upgrade": {"type": "boolean", "description": "Explicitly request a supervised upgrade of an installed package."},
        "preserveLegacyFiles": {"type": "boolean", "description": "Opt in to a verified complete backup outside Assets before legacy package migration, preserving unknown and modified files. Requires legacy baseline inputs; does not bypass checkpoint or approval."},
        "legacyBaselineArchive": {"type": "string", "description": "Official old UnityPackage archive used to verify an Assets-imported upgrade."},
        "legacyBaselineAssetsRoot": {"type": "string", "description": "Exact existing Assets package root verified against the baseline archive."},
    },
}
