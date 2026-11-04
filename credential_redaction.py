"""Credential-pattern scrubbing for export and persistence boundaries.

The model face passes execution data through (a454051); this module is the
shared scrubber for the layers that must redact: dashboard text exports and
background-goal durable persistence. Patterns are recovered from the
pre-a454051 sanitizer where noted; the assignment pattern additionally
tolerates quoted and JSON-escaped assignments ("apiKey": "v",
$api_key='v') so serialized observations are covered.
"""
from __future__ import annotations

import re

_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(secret|token|password|credential|api[_-]?key)[\s\"'\\]*[:=][\s\"'\\]*[^\s,\"';]+"
)
_CREDENTIAL_WORD = re.compile(r"(?i)\b(secret|token|password|credential)\b")
# Token-shaped credentials recovered from the pre-a454051 sanitizer: bare
# provider keys have no key= prefix, so the assignment pattern misses them.
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]+")
_KNOWN_TOKEN = re.compile(
    # Hyphens belong to opaque identifiers; a word boundary inside one is not
    # a standalone credential boundary. Sensitive fields remain redacted above.
    r"(?<![\w-])(?:(?:sk-(?:proj-)?|gh[pousr]_|github_pat_|hf_|xox[baprs]-)[A-Za-z0-9_-]{4,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,})",
    re.IGNORECASE,
)
_JWT = re.compile(r"(?<![\w-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
# Absolute-path patterns recovered from the pre-a454051 sanitizer; used by
# the persistence boundary, where user paths are private. Dashboard text
# keeps paths (its current contract); support exports own full-path policy.
_WINDOWS_PATH = re.compile(r"(?<![\w])(?:[a-z]:[\\/]|\\\\)[^\s,;]+", re.IGNORECASE)
_UNIX_PATH = re.compile(r"(?<![\w:])/(?:[^\s,;]+)")


def redact_credential_text(value: str) -> str:
    scrubbed = _CREDENTIAL_ASSIGNMENT.sub(r"\1=[REDACTED]", value)
    scrubbed = _CREDENTIAL_WORD.sub("[REDACTED]", scrubbed)
    scrubbed = _BEARER_TOKEN.sub("Bearer <redacted>", scrubbed)
    scrubbed = _KNOWN_TOKEN.sub("<redacted>", scrubbed)
    return _JWT.sub("<redacted>", scrubbed)


def redact_persistence_text(value: str) -> str:
    """Credential plus absolute-path scrubbing for durable state prose."""
    scrubbed = redact_credential_text(value)
    scrubbed = _WINDOWS_PATH.sub("<path redacted>", scrubbed)
    return _UNIX_PATH.sub("<path redacted>", scrubbed)
