"""Bounded immutable state pages for captured runtime observation receipts."""
from __future__ import annotations

import copy
import hashlib
import json
import re

RESOURCE_TYPE = "runtime_observation_state_page"
PAGE_LIMIT = 16
MAX_RESOURCE_BYTES = 32768


class StateSelectionError(ValueError):
    """A requested inline state selector is absent, ambiguous, or unstable."""

    def __init__(self, code, message, candidates=()):
        super().__init__(message)
        self.code = code
        self.candidates = tuple(candidates)
        self.details = {"candidates": list(self.candidates)} if self.candidates else {}
        self.retryable = False


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _uri(job, frame, offset):
    return f"vrcforge://runtime-observation/{job}/frames/{frame}/states?offset={offset}&limit={PAGE_LIMIT}"


def read_state_page(envelope):
    data = envelope["data"]
    if envelope.get("contentHash") != _hash({"identity": envelope["identity"], "data": data}):
        raise ValueError("Captured state resource content or identity changed.")
    expected = _uri(data["jobId"], data["frameIndex"], data["offset"]) + "&revision=1"
    if envelope["uri"] != expected or envelope["identity"].get("jobId") != data["jobId"]:
        raise ValueError("Captured state resource URI identity differs.")
    if data["actualCount"] != len(data["states"]) or not 0 < data["actualCount"] <= PAGE_LIMIT:
        raise ValueError("Captured state page count differs.")
    encoded = _encoded(envelope)
    if len(encoded) > MAX_RESOURCE_BYTES:
        raise ValueError("Captured state page exceeds its bounded resource size.")
    from mcp_resource_registry import RESOURCE_MIME_TYPE
    return {"contents": [{"uri": envelope["uri"], "mimeType": RESOURCE_MIME_TYPE, "text": encoded.decode("utf-8")}]}


def _state_identity(row):
    return {key: row.get(key) for key in ("controllerIndex", "layerIndex", "layerName")}


def _bounded_candidates(rows):
    return tuple(_state_identity(row) for row in rows[:8] if isinstance(row, dict))


def _select_inline_states(result, selection):
    """Filter a validated full receipt while retaining source and selected hashes."""
    if selection is None:
        return result
    layer_name = selection["layerName"]
    frames = result.get("frames")
    if not isinstance(frames, list) or not frames:
        raise StateSelectionError("state_selection_no_frames", "stateSelection requires complete frame state evidence.")
    first_states = frames[0].get("states")
    if not isinstance(first_states, list) or any(not isinstance(row, dict) for row in first_states):
        raise StateSelectionError("state_selection_incomplete", "stateSelection requires complete state rows in the first frame.")
    first_rows = [row for row in first_states if row.get("layerName") == layer_name]
    if len(first_rows) != 1:
        code = "state_selection_missing" if not first_rows else "state_selection_ambiguous"
        raise StateSelectionError(code, f"stateSelection.layerName {layer_name!r} matched {len(first_rows)} rows in the first frame; selection is not complete or unique.", _bounded_candidates(first_rows))
    identity = _state_identity(first_rows[0])
    selected_hashes = []
    source_counts = []
    selected_counts = []
    for index, frame in enumerate(frames):
        states = frame.get("states")
        if not isinstance(states, list) or any(not isinstance(row, dict) for row in states):
            raise StateSelectionError("state_selection_incomplete", f"stateSelection requires complete state rows in frame {index}.")
        source_counts.append(len(states))
        matches = [row for row in states if row.get("layerName") == layer_name]
        if len(matches) != 1:
            code = "state_selection_missing" if not matches else "state_selection_ambiguous"
            raise StateSelectionError(code, f"stateSelection.layerName {layer_name!r} matched {len(matches)} rows in frame {index}; selection is not complete or unique.", _bounded_candidates(matches))
        if _state_identity(matches[0]) != identity:
            raise StateSelectionError("state_selection_identity_drift", f"stateSelection.layerName {layer_name!r} changed controller/layer identity in frame {index}.", _bounded_candidates(matches))
        frame["states"] = [matches[0]]
        frame["sourceStateCount"] = len(states)
        frame["selectedStateCount"] = len(frame["states"])
        selected_counts.append(len(frame["states"]))
        frame["selectedStateHash"] = _hash(frame["states"])
        selected_hashes.append(frame["selectedStateHash"])
    result["stateSelection"] = {"layerName": layer_name, "status": "selected", "complete": True,
                                 "selectedIdentity": identity, "sourceStateCountPerFrame": source_counts,
                                 "selectedStateCountPerFrame": selected_counts,
                                 "selectedStateSetHash": _hash(selected_hashes)}
    result["stateSelectionStatus"] = "selected"
    return result


def project_receipt(registry, receipt, execution_target, *, state_detail="resource", state_selection=None):
    """Project only after the caller's complete domain readback validation.

    Pending/partial captured rows remain explicitly nonverified; this function
    never establishes observation success or changes lifecycle facts.
    """
    if state_detail not in ("inline", "resource"):
        raise ValueError("stateDetail must be inline or resource.")
    if state_selection is not None:
        if not isinstance(state_selection, dict) or set(state_selection) != {"layerName"} or not isinstance(state_selection.get("layerName"), str) or not state_selection["layerName"].strip():
            raise StateSelectionError("state_selection_invalid", "stateSelection requires exactly one non-empty layerName.")
        if state_detail != "inline":
            raise StateSelectionError("state_selection_requires_inline", "stateSelection is only available with stateDetail=inline; resource mode returns complete page URIs.")
    result = copy.deepcopy(receipt)
    if state_detail == "inline":
        return _select_inline_states(result, state_selection)
    frames = result.get("frames", [])
    if not frames:
        result.update(stateDetail="resource", statesInline=False)
        return result
    job = result.get("jobId", "")
    if result.get("schema") != "vrcforge.runtime_observation.v1" or not re.fullmatch(r"[0-9a-f]{32}", job) or not result.get("avatarPath"):
        raise ValueError("Captured state receipt identity missing.")
    if not isinstance(frames, list) or len(frames) > 33:
        raise ValueError("Captured state frames exceed observation bound.")
    if result.get("status") == "completed" and (result.get("verified") is not True or not isinstance(result.get("readback"), dict)):
        raise ValueError("Completed state evidence must be validated before projection.")
    identity = {"executionTarget": execution_target or {}, "jobId": job,
                "avatarPath": result["avatarPath"], "coreIdentity": result.get("coreIdentity")}
    sealed = []
    for index, frame in enumerate(frames):
        states = frame.get("states")
        if not isinstance(states, list) or not 1 <= len(states) <= 256 or any(not isinstance(row, dict) for row in states):
            raise ValueError("Captured state rows missing or outside observation bound.")
        state_hash = _hash(states)
        first_uri = _uri(job, index, 0) + "&revision=1"
        # Re-reading a job must not replace its original historical capture.
        from mcp_resource_registry import McpResourceError
        try:
            existing = json.loads(registry.read(first_uri)["contents"][0]["text"])
        except McpResourceError:
            existing = None
        if existing is not None:
            data = existing["data"]
            if existing["identity"] != identity or data["stateSetHash"] != state_hash or data["unityFrame"] != frame["unityFrame"] or data["sampleIndex"] != frame["sampleIndex"]:
                raise ValueError("Captured job/frame state identity changed; original evidence preserved.")
            # Verify every registered page, including after registry restart.
            uri = first_uri
            restored = []
            while uri:
                page = json.loads(registry.read(uri)["contents"][0]["text"])["data"]
                if page["offset"] != len(restored) or page["stateSetHash"] != state_hash:
                    raise ValueError("Captured state page sequence changed.")
                restored.extend(page["states"])
                uri = page["nextUri"]
            if restored != states:
                raise ValueError("Captured state page rows changed.")
        else:
            offset = 0
            while offset < len(states):
                count = min(PAGE_LIMIT, len(states) - offset)
                while count:
                    next_offset = offset + count
                    data = {"jobId": job, "frameIndex": index, "sampleIndex": frame["sampleIndex"],
                            "unityFrame": frame["unityFrame"], "actualElapsedSeconds": frame["actualElapsedSeconds"],
                            "offset": offset, "limit": PAGE_LIMIT, "actualCount": count, "totalCount": len(states),
                            "stateSetHash": state_hash, "states": states[offset:next_offset],
                            "nextUri": _uri(job, index, next_offset) + "&revision=1" if next_offset < len(states) else None}
                    # Reserve envelope overhead; enforce the exact final size on read.
                    if len(_encoded({"identity": identity, "data": data})) <= MAX_RESOURCE_BYTES - 4096:
                        break
                    count -= 1
                if not count:
                    raise ValueError("One captured state exceeds the page byte limit; inline evidence remains available.")
                sealed.append((_uri(job, index, offset), data))
                offset += count
        frame.pop("states")
        frame.update(stateCount=len(states), stateDetail="resource", statesInline=False,
                     statesResourceUri=first_uri, stateSetHash=state_hash)
    # All frame data and existing pages have been checked before any publication.
    entries = [dict(base_uri=base_uri, name=f"Runtime states {job} frame {data['frameIndex']} offset {data['offset']}",
        resource_type=RESOURCE_TYPE, data=data, identity=identity, source_mode="captured_runtime_states",
        refresh_rule="Historical state evidence retained in App user data until removed; reading never changes Unity.",
        description="One bounded page of actual captured controller/layer states, with original clip metadata and weights.", only_if_absent=True)
        for base_uri, data in sealed]
    def validate_pages(envelopes):
        for envelope, (_, data) in zip(envelopes, sealed):
            if envelope["data"] != data or envelope["identity"] != identity:
                raise ValueError("Captured state resource was concurrently replaced.")
            read_state_page(envelope)
    registry.publish_many(entries, validate_records=validate_pages)
    result.update(stateDetail="resource", statesInline=False)
    return result
