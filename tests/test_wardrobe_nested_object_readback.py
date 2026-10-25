"""Nested Unity-object members must compare through identity channels."""
from wardrobe_outfit_workflow_service import _requested_value_matches


def test_shapes_object_reference_intent_matches_readback_description():
    observed_shapes = [
        {
            "Object": {
                "instanceID": -1234,
                "name": "Milltina_body",
                "assetPath": "",
                "assetGuid": "",
                "localFileId": 0,
                "globalObjectId": "GlobalObjectId_V1-2-abc-111-0",
                "hierarchyPath": "Milltina/Milltina_body",
                "scenePath": "Assets/Milltina_LocalFit_Work.unity",
            },
            "ShapeName": "(Wear bra)Breasts_Cow",
            "ChangeType": 1,
            "Value": 0.0,
        }
    ]
    # Write request uses the referencePath vocabulary for the nested Object member.
    expected_shapes = [
        {"Object": {"referencePath": "Milltina/Milltina_body"},
         "ShapeName": "(Wear bra)Breasts_Cow", "ChangeType": 1, "Value": 0}
    ]
    assert _requested_value_matches(observed_shapes, expected_shapes, {"": None})


def test_object_reference_intent_still_rejects_wrong_identity():
    observed = {"Object": {"hierarchyPath": "Milltina/Other_body", "assetPath": "", "globalObjectId": "x"}}
    expected = [{"Object": {"referencePath": "Milltina/Milltina_body"}}]
    assert not _requested_value_matches([observed], expected)


def test_globalObjectId_intent_matches_exactly():
    observed = {"globalObjectId": "GlobalObjectId_V1-2-abc-111-0", "hierarchyPath": "a/b"}
    assert _requested_value_matches(observed, {"globalObjectId": "GlobalObjectId_V1-2-abc-111-0"})
    assert not _requested_value_matches(observed, {"globalObjectId": "GlobalObjectId_V1-2-abc-222-0"})


def test_non_identity_structured_members_still_require_exact_keys():
    assert not _requested_value_matches({"Value": 0.0}, {"Value": 1})
