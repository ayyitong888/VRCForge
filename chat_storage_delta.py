"""Strict item-scoped JSON Patch materialization for chat storage CAS."""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

import jsonpatch

from chat_snapshot_transport import chat_content_digest


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PATCH_OPS = {"add", "remove", "replace"}


class ChatStoragePatchError(ValueError):
    """A client patch cannot be applied without risking an overwrite."""

    status_code = 422

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _pointer_path(value: Any, *, allow_root: bool = False) -> str:
    if not isinstance(value, str) or (not allow_root and not value.startswith("/")):
        raise ChatStoragePatchError("chat patch JSON Pointer path is invalid")
    if not allow_root and value == "":
        raise ChatStoragePatchError("chat patch root replacement is forbidden")
    return value


def _validate_operations(operations: Any, *, item: bool) -> list[dict[str, Any]]:
    if not isinstance(operations, list):
        raise ChatStoragePatchError("chat patch operations must be an array")
    validated: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ChatStoragePatchError("chat patch operation must be an object")
        op = operation.get("op")
        if op not in _PATCH_OPS:
            raise ChatStoragePatchError("chat patch operation is unsupported")
        path = _pointer_path(operation.get("path"))
        allowed_keys = {"op", "path"}
        if op in {"add", "replace"}:
            allowed_keys.add("value")
        if set(operation) - allowed_keys:
            raise ChatStoragePatchError("chat patch operation has unknown fields")
        if item:
            first = path.split("/", 2)[1]
            if first in {"id", "type"}:
                if first == "id":
                    raise ChatStoragePatchError("chat item id is immutable")
                # Existing UI semantics permit an explicit item type change.
        else:
            first = path.split("/", 2)[1]
            if first in {"id", "items"}:
                raise ChatStoragePatchError("chat metadata patch cannot modify id or items")
        if op in {"add", "replace"} and "value" not in operation:
            raise ChatStoragePatchError("chat patch operation value is required")
        validated.append(copy.deepcopy(operation))
    return validated


def _apply_operations(document: dict[str, Any], operations: Any, *, item: bool) -> dict[str, Any]:
    validated = _validate_operations(operations, item=item)
    try:
        result = jsonpatch.JsonPatch(validated).apply(document, in_place=False)
    except (jsonpatch.JsonPatchException, TypeError, ValueError) as exc:
        raise ChatStoragePatchError("chat patch failed against the current raw record") from exc
    if not isinstance(result, dict):
        raise ChatStoragePatchError("chat patch must leave an object record")
    return result


def _validate_patch_envelope(chat_id: str, patch: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]], list[str], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    if not isinstance(patch, Mapping):
        raise ChatStoragePatchError(f"chat patch is invalid for {chat_id}")
    base_digest = patch.get("baseDigest")
    if not isinstance(base_digest, str) or not _DIGEST_RE.fullmatch(base_digest):
        raise ChatStoragePatchError(f"chat patch baseDigest is invalid for {chat_id}")
    metadata_patch = _validate_operations(patch.get("metadataPatch"), item=False)
    item_order = patch.get("itemOrder")
    if not isinstance(item_order, list) or any(not isinstance(item_id, str) or not item_id.strip() for item_id in item_order):
        raise ChatStoragePatchError(f"chat patch itemOrder is invalid for {chat_id}")
    if len(set(item_order)) != len(item_order):
        raise ChatStoragePatchError(f"chat patch itemOrder contains duplicate ids for {chat_id}")
    item_patches = patch.get("itemPatches")
    if not isinstance(item_patches, dict):
        raise ChatStoragePatchError(f"chat patch itemPatches is invalid for {chat_id}")
    validated_item_patches: dict[str, list[dict[str, Any]]] = {}
    for item_id, operations in item_patches.items():
        if not isinstance(item_id, str) or not item_id.strip():
            raise ChatStoragePatchError(f"chat patch itemPatches contains an invalid item id for {chat_id}")
        validated_item_patches[item_id] = _validate_operations(operations, item=True)
    new_items = patch.get("newItems")
    if not isinstance(new_items, dict):
        raise ChatStoragePatchError(f"chat patch newItems is invalid for {chat_id}")
    validated_new_items: dict[str, dict[str, Any]] = {}
    for item_id, item in new_items.items():
        if not isinstance(item_id, str) or not item_id.strip() or not isinstance(item, dict) or item.get("id") != item_id:
            raise ChatStoragePatchError(f"chat patch newItems identity is invalid for {chat_id}")
        validated_new_items[item_id] = copy.deepcopy(item)
    return base_digest, metadata_patch, item_order, validated_item_patches, validated_new_items


def _materialize_one(current: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    chat_id = str(current.get("id") or "")
    base_digest, metadata_patch, item_order, item_patches, new_items = _validate_patch_envelope(chat_id, patch)
    if chat_content_digest(current) != base_digest:
        raise ChatStoragePatchError(f"chat patch base changed for {chat_id}", status_code=409)
    items = current.get("items")
    if not isinstance(items, list):
        raise ChatStoragePatchError(f"chat items are invalid for {chat_id}")
    existing_items: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip() or item["id"] in existing_items:
            raise ChatStoragePatchError(f"chat item identity is invalid for {chat_id}")
        existing_items[item["id"]] = copy.deepcopy(item)
    if set(item_patches) - set(existing_items):
        raise ChatStoragePatchError(f"chat patch references an unknown existing item for {chat_id}")
    if set(new_items) & set(existing_items):
        raise ChatStoragePatchError(f"chat patch new item collides with an existing item for {chat_id}")
    retained_existing = set(item_order) & set(existing_items)
    if set(item_patches) - retained_existing:
        raise ChatStoragePatchError(f"chat patch modifies an item removed by itemOrder for {chat_id}")
    if set(item_order) != retained_existing | set(new_items):
        raise ChatStoragePatchError(f"chat patch itemOrder does not match item patches and new items for {chat_id}")

    materialized_items: dict[str, dict[str, Any]] = {}
    for item_id in retained_existing:
        item = existing_items[item_id]
        if item_id in item_patches:
            item = _apply_operations(item, item_patches[item_id], item=True)
            if item.get("id") != item_id:
                raise ChatStoragePatchError(f"chat item id changed for {chat_id}")
        materialized_items[item_id] = item
    materialized_items.update(copy.deepcopy(new_items))
    result = _apply_operations({**copy.deepcopy(current), "items": []}, metadata_patch, item=False)
    result["items"] = [materialized_items[item_id] for item_id in item_order]
    return result


def materialize_chat_patches(
    current_chats: Sequence[dict[str, Any]],
    requested_chats: Sequence[dict[str, Any]],
    patches: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize a full desired chat list from raw server chats and deltas."""
    current_by_id: dict[str, dict[str, Any]] = {}
    for chat in current_chats:
        chat_id = str(chat.get("id") or "")
        if not chat_id or chat_id in current_by_id:
            raise ChatStoragePatchError("current chat ids are duplicated or invalid")
        current_by_id[chat_id] = copy.deepcopy(chat)
    requested_ids = [str(chat.get("id") or "") for chat in requested_chats]
    if any(not chat_id for chat_id in requested_ids) or len(set(requested_ids)) != len(requested_ids):
        raise ChatStoragePatchError("requested chat ids are duplicated or invalid")
    patch_ids = set(patches)
    existing_requested = {chat_id for chat_id in requested_ids if chat_id in current_by_id}
    if patch_ids - set(current_by_id) or patch_ids != existing_requested:
        raise ChatStoragePatchError("chat patches do not exactly cover existing requested chats")
    result: list[dict[str, Any]] = []
    for chat in requested_chats:
        chat_id = str(chat.get("id") or "")
        if chat_id in current_by_id:
            result.append(_materialize_one(current_by_id[chat_id], patches[chat_id]))
        else:
            if chat_id in patch_ids:
                raise ChatStoragePatchError("new chats cannot carry patches")
            result.append(copy.deepcopy(chat))
    return result
