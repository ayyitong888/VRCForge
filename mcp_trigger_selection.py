"""Selection-only MCP planner probe used by release acceptance."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from runtime_planner_service import parse_llm_plan_response


class SelectionReceiptAuthority:
    """Process-owned, one-use evidence for selection-only provider calls."""

    def __init__(self, *, ttl_seconds: int = 900, max_receipts: int = 256) -> None:
        self._ttl_seconds = max(30, min(int(ttl_seconds), 3600))
        self._max_receipts = max(40, min(int(max_receipts), 4096))
        self._secret = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._receipts: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _digest(value: Any) -> str:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _signature(self, payload: Mapping[str, Any]) -> str:
        canonical = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(self._secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(
        self,
        message: str,
        visible_tools: Sequence[Mapping[str, Any]],
        result: Mapping[str, Any],
        *,
        provider: str,
        model: str,
        config_digest: str,
        resolved_api_type: str,
        exposure_layer: str = "planning",
    ) -> dict[str, Any]:
        tools, tools_hash = _canonical_tools(visible_tools)
        calls = result.get("toolCalls")
        if not isinstance(calls, list) or any(not isinstance(item, str) for item in calls):
            raise RuntimeError("selection receipt requires validated toolCalls")
        now = int(time.time())
        payload = {
            "schema": "vrcforge.mcp_selection_receipt.v1",
            "receiptId": secrets.token_hex(16),
            "provider": str(provider or "").strip(),
            "model": str(model or "").strip(),
            "configDigest": str(config_digest or "").strip(),
            "resolvedApiType": str(resolved_api_type or "").strip(),
            "exposureLayer": normalize_exposure_layer(exposure_layer),
            "selectionOnly": True,
            "toolsExecuted": False,
            "visibleToolsHash": tools_hash,
            "visibleToolCount": len(tools),
            "requestDigest": self._digest({"message": str(message), "visibleToolsHash": tools_hash}),
            "responseDigest": self._digest({"toolCalls": calls}),
            "issuedAt": now,
            "expiresAt": now + self._ttl_seconds,
        }
        if not all(payload[key] for key in ("provider", "model", "configDigest", "resolvedApiType")):
            raise RuntimeError("selection receipt requires the frozen nonsecret provider configuration")
        signature = self._signature(payload)
        with self._lock:
            self._prune_locked(now)
            self._receipts[payload["receiptId"]] = {"payload": payload, "signature": signature}
        return {"source": "dashboard-selection-receipt", **payload, "signature": signature}

    def verify_and_consume(
        self,
        message: str,
        visible_tools: Sequence[Mapping[str, Any]],
        result: Mapping[str, Any],
        *,
        provider: str,
        model: str,
        config_digest: str,
        resolved_api_type: str,
        exposure_layer: str = "planning",
    ) -> bool:
        evidence = result.get("providerEvidence")
        calls = result.get("toolCalls")
        if not isinstance(evidence, Mapping) or not isinstance(calls, list):
            return False
        current_binding = {
            "provider": str(provider or "").strip(),
            "model": str(model or "").strip(),
            "configDigest": str(config_digest or "").strip(),
            "resolvedApiType": str(resolved_api_type or "").strip(),
            "exposureLayer": normalize_exposure_layer(exposure_layer),
        }
        if not all(current_binding.values()):
            return False
        receipt_id = str(evidence.get("receiptId") or "")
        if not receipt_id:
            return False
        now = int(time.time())
        with self._lock:
            self._prune_locked(now)
            stored = self._receipts.get(receipt_id)
            if not isinstance(stored, dict):
                return False
            payload = stored.get("payload")
            signature = str(stored.get("signature") or "")
            if (
                not isinstance(payload, dict)
                or not signature
                or not hmac.compare_digest(signature, self._signature(payload))
            ):
                return False
            if any(
                not hmac.compare_digest(str(payload.get(key) or ""), value)
                for key, value in current_binding.items()
            ):
                return False
            tools, tools_hash = _canonical_tools(visible_tools)
            allowed_evidence_keys = {"source", *payload.keys(), "signature"}
            if set(evidence) != allowed_evidence_keys:
                return False
            expected = {
                **payload,
                "visibleToolsHash": tools_hash,
                "visibleToolCount": len(tools),
                "requestDigest": self._digest({"message": str(message), "visibleToolsHash": tools_hash}),
                "responseDigest": self._digest({"toolCalls": calls}),
            }
            public_evidence = {key: evidence.get(key) for key in allowed_evidence_keys}
            expected_evidence = {"source": "dashboard-selection-receipt", **expected, "signature": signature}
            accepted = (
                now <= int(payload.get("expiresAt") or 0)
                and public_evidence == expected_evidence
                and hmac.compare_digest(signature, self._signature(expected))
            )
            if accepted:
                self._receipts.pop(receipt_id, None)
            return accepted

    def _prune_locked(self, now: int) -> None:
        expired = [key for key, value in self._receipts.items() if int(value["payload"].get("expiresAt") or 0) < now]
        for key in expired:
            self._receipts.pop(key, None)
        overflow = len(self._receipts) - self._max_receipts + 1
        if overflow > 0:
            for key in list(self._receipts)[:overflow]:
                self._receipts.pop(key, None)


def _canonical_tools(tools: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    normalized: list[dict[str, Any]] = []
    for item in tools:
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("selection-only tools require non-empty names")
        normalized.append({str(key): item[key] for key in sorted(item)})
    normalized.sort(key=lambda item: str(item["name"]))
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return normalized, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_exposure_layer(value: Any) -> str:
    layer = str(value or "planning").strip().lower()
    if layer not in {"planning", "execution"}:
        raise ValueError("exposureLayer must be planning or execution")
    return layer


def tools_for_exposure_layer(
    visible_tools: Sequence[Mapping[str, Any]],
    exposure_layer: str,
) -> list[dict[str, Any]]:
    layer = normalize_exposure_layer(exposure_layer)
    tools, _ = _canonical_tools(visible_tools)
    if layer == "execution":
        return tools
    planning: list[dict[str, Any]] = []
    try:
        from unity_mcp_tool_contract import EXPECTED_TOOL_NAMES, PLANNING_TOOL_NAMES
    except ImportError:  # pragma: no cover - standalone selection utility.
        EXPECTED_TOOL_NAMES = frozenset()
        PLANNING_TOOL_NAMES = frozenset()
    for tool in tools:
        name = str(tool.get("name") or "")
        if name in EXPECTED_TOOL_NAMES:
            if name in PLANNING_TOOL_NAMES:
                planning.append(tool)
            continue
        metadata = tool.get("_meta") if isinstance(tool.get("_meta"), Mapping) else {}
        annotations = tool.get("annotations") if isinstance(tool.get("annotations"), Mapping) else {}
        if metadata.get("exposureLayer") == "planning" or annotations.get("readOnlyHint") is True:
            planning.append(tool)
    return planning


def plan_mcp_tool_selection(
    message: str,
    visible_tools: Sequence[Mapping[str, Any]],
    *,
    provider: str,
    model: str,
    request_text: Callable[[str], Any],
) -> dict[str, Any]:
    """Ask one real provider to select names without entering any execution path."""

    provider = str(provider or "").strip()
    model = str(model or "").strip()
    if not provider or not model:
        raise RuntimeError("configured provider and model are required for selection evidence")
    tools, tools_hash = _canonical_tools(visible_tools)
    prompt = (
        "You are a selection-only VRCForge MCP router. Never execute a tool and never claim that a tool ran. "
        "Choose a tool only when the user explicitly asks to inspect or change the current Unity project. "
        "Questions, explanations, translations, hypotheticals, quoted tool names, and explicit no-tool requests "
        "must return no calls. Return exactly one JSON object: {\"toolCalls\":[\"exact_tool_name\"]} or "
        "{\"toolCalls\":[]}. Select at most one name and only from the frozen list below.\n\n"
        + "Frozen MCP tools:\n"
        + json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n\nUser message:\n"
        + str(message)
    )
    response = request_text(prompt)
    response_text = str(getattr(response, "text", response) or "")
    payload = parse_llm_plan_response(response_text)
    if not isinstance(payload, dict):
        raise RuntimeError("selection provider did not return a JSON object")
    calls = payload.get("toolCalls") or payload.get("tool_calls") or []
    if str(payload.get("action") or "").strip().lower() == "skill":
        calls = [payload.get("skill_tool") or payload.get("skillTool")]
    if not isinstance(calls, list):
        raise RuntimeError("selection provider toolCalls must be a list")
    selected = [str(item or "").strip() for item in calls if str(item or "").strip()]
    known = {str(item["name"]) for item in tools}
    if len(selected) > 1 or any(name not in known for name in selected):
        raise RuntimeError("selection provider returned an unknown or multi-tool result")
    return {
        "toolCalls": selected,
        "providerEvidence": {
            "source": "dashboard-llm-plan",
            "provider": provider,
            "model": model,
            "selectionOnly": True,
            "toolsExecuted": False,
            "visibleToolsHash": tools_hash,
        },
    }
