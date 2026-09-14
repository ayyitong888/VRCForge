"""Expression receipt production wiring and persistence regression."""
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("file,cls",[("UnityAvatarPrimitiveCrud.cs","ManageExpressionMenuTool"),("UnityAvatarPrimitiveCrud.cs","ManageExpressionParametersTool"),("UnityAvatarAuthoringCrud.cs","EnsureExpressionParameterTool"),("UnityAvatarAuthoringCrud.cs","EnsureExpressionMenuControlTool")])
def test_expression_success_is_bound_to_persisted_verification(file,cls):
    source=(ROOT/"Assets/VRCForge/Editor/Generic"/file).read_text(encoding="utf-8")
    body=source.split("public static class "+cls,1)[1].split("public static class ",1)[0]
    assert "ExpressionWritePersistence.SaveAndVerify" in body
    assert 'schema = "vrcforge.expression_write.v1"' in body
    assert "readback = persistedReadback" in body
    assert "AssetDatabase.SaveAssets()" not in body
