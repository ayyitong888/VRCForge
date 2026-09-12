from wardrobe_outfit_workflow_service import _build_create_wardrobe_core_calls_from_request


def test_wardrobe_create_uses_the_core_avatar_and_asset_type_layout():
    calls = _build_create_wardrobe_core_calls_from_request(
        {"avatarPath": "Scene/FinalAvatar", "parameterName": "Wardrobe"}, preview=True
    )
    assert calls
    assert all("assetDir" not in arguments for _, arguments in calls)


def test_wardrobe_create_preserves_an_explicit_user_output_root():
    calls = _build_create_wardrobe_core_calls_from_request(
        {"avatarPath": "Scene/FinalAvatar", "parameterName": "Wardrobe", "assetDir": "Assets/MyAvatarAssets"},
        preview=True,
    )
    assert calls
    assert all(arguments["assetDir"] == "Assets/MyAvatarAssets" for _, arguments in calls)
