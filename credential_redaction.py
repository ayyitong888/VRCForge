"""Credential-pattern scrubbing for export and persistence boundaries.

Execution data remains intact for the model; exports and background-goal
persistence use the existing diagnostic credential policy. JSON-escaped
observations are decoded before that same policy is applied.
"""
from __future__ import annotations

import json
import re

from diagnostic_privacy import redact_credential_text as redact_diagnostic_credentials

# Valid JSON string-content spans, including escaped quote delimiters. Matching
# spans rather than whole lines also handles encoded objects embedded in prose.
_JSON_STRING_CONTENT = re.compile(r'(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|[^"\\\x00-\x1f])+')
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
    def redact_encoded(match: re.Match[str]) -> str:
        original = match.group(0)
        if "\\" not in original:
            return original
        decoded = json.loads('"' + original + '"')
        scrubbed = redact_diagnostic_credentials(decoded)
        return json.dumps(scrubbed, ensure_ascii=False)[1:-1] if scrubbed != decoded else original

    scrubbed = _JSON_STRING_CONTENT.sub(redact_encoded, value)
    scrubbed = redact_diagnostic_credentials(scrubbed)
    scrubbed = _BEARER_TOKEN.sub("Bearer <redacted>", scrubbed)
    scrubbed = _KNOWN_TOKEN.sub("<redacted>", scrubbed)
    return _JWT.sub("<redacted>", scrubbed)


def redact_persistence_text(value: str) -> str:
    """Credential plus absolute-path scrubbing for durable state prose."""
    scrubbed = redact_credential_text(value)
    scrubbed = _WINDOWS_PATH.sub("<path redacted>", scrubbed)
    return _UNIX_PATH.sub("<path redacted>", scrubbed)
