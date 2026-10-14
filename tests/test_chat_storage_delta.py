import pytest

from chat_snapshot_transport import chat_content_digest
from chat_storage_delta import ChatStoragePatchError, materialize_chat_patches


def _chat(*, title="Product persistence regression 072", items=None):
    return {
        "id": "chat-072",
        "title": title,
        "items": items or [
            {
                "id": "user-1",
                "type": "user",
                "text": "remember the config field name api_key and the phrase morning sea salt",
            },
            {"id": "assistant-1", "type": "assistant", "text": "The field was api_key."},
        ],
    }


def _patch(chat, **overrides):
    value = {
        "baseDigest": chat_content_digest(chat),
        "metadataPatch": [],
        "itemOrder": [item["id"] for item in chat["items"]],
        "itemPatches": {},
        "newItems": {},
    }
    value.update(overrides)
    return value


def test_existing_chat_uses_raw_server_record_and_preserves_api_key_field_text():
    current = _chat()
    patch = _patch(current, metadataPatch=[{"op": "replace", "path": "/title", "value": "Remembered"}])
    result = materialize_chat_patches(
        [current],
        [{"id": current["id"], "title": "client projection", "items": []}],
        {current["id"]: patch},
    )
    assert result[0]["title"] == "Remembered"
    assert result[0]["items"] == current["items"]


def test_item_patch_can_update_summary_and_reorder_with_new_item():
    current = _chat()
    patch = _patch(
        current,
        itemOrder=["assistant-1", "user-1", "compact-1"],
        itemPatches={"assistant-1": [{"op": "replace", "path": "/text", "value": "The field was `api_key`."}]},
        newItems={"compact-1": {"id": "compact-1", "type": "compact", "text": "api_key remembered"}},
    )
    result = materialize_chat_patches([current], [{"id": current["id"]}], {current["id"]: patch})
    assert [item["id"] for item in result[0]["items"]] == ["assistant-1", "user-1", "compact-1"]
    assert result[0]["items"][0]["text"] == "The field was `api_key`."


def test_base_digest_mismatch_is_conflict_and_rejects_metadata_root_paths():
    current = _chat()
    stale = _patch(current, baseDigest="0" * 64)
    with pytest.raises(ChatStoragePatchError) as conflict:
        materialize_chat_patches([current], [{"id": current["id"]}], {current["id"]: stale})
    assert conflict.value.status_code == 409

    forbidden = _patch(current, metadataPatch=[{"op": "replace", "path": "/items", "value": []}])
    with pytest.raises(ChatStoragePatchError):
        materialize_chat_patches([current], [{"id": current["id"]}], {current["id"]: forbidden})


@pytest.mark.parametrize("requested, patches", [
    ([{"id": "chat-072"}], {}),
    ([{"id": "new-chat"}], {"new-chat": _patch(_chat())}),
    ([{"id": "new-chat"}], {"chat-072": _patch(_chat())}),
    ([], {"chat-072": _patch(_chat())}),
])
def test_patch_coverage_is_exact(requested, patches):
    current = _chat()
    if "new-chat" in patches:
        patches["new-chat"]["baseDigest"] = chat_content_digest(current)
    with pytest.raises(ChatStoragePatchError) as error:
        materialize_chat_patches([current], requested, patches)
    assert error.value.status_code == 422


def test_item_id_is_immutable_but_type_change_is_explicitly_allowed():
    current = _chat()
    allowed = _patch(
        current,
        itemPatches={"assistant-1": [{"op": "replace", "path": "/type", "value": "plan"}]},
    )
    result = materialize_chat_patches([current], [{"id": current["id"]}], {current["id"]: allowed})
    assert result[0]["items"][1]["type"] == "plan"

    forbidden = _patch(
        current,
        itemPatches={"assistant-1": [{"op": "replace", "path": "/id", "value": "other"}]},
    )
    with pytest.raises(ChatStoragePatchError):
        materialize_chat_patches([current], [{"id": current["id"]}], {current["id"]: forbidden})


def test_patch_does_not_allow_copy_or_move_to_escape_item_boundaries():
    current = _chat()
    for operation in (
        {"op": "copy", "from": "/items/0/text", "path": "/title"},
        {"op": "move", "from": "/items/0/text", "path": "/title"},
    ):
        forbidden = _patch(current, metadataPatch=[operation])
        with pytest.raises(ChatStoragePatchError):
            materialize_chat_patches([current], [{"id": current["id"]}], {current["id"]: forbidden})
