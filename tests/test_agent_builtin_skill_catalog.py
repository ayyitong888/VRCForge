"""Builtin metadata must remain shared and independently importable."""
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_catalog_import_does_not_initialize_the_gateway():
    result = subprocess.run([
        sys.executable, "-c",
        "import sys; import agent_builtin_skill_catalog; "
        "assert 'agent_gateway' not in sys.modules; "
        "assert 'agent_skill_registry' not in sys.modules",
    ], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_gateway_and_registry_share_the_catalog_objects():
    import agent_builtin_skill_catalog as catalog
    import agent_gateway as gateway
    import agent_skill_registry as registry

    assert gateway.BUILTIN_SKILL_GROUPS is registry.BUILTIN_SKILL_GROUPS is catalog.BUILTIN_SKILL_GROUPS
    assert gateway.BUILTIN_SKILL_OVERRIDES is registry.BUILTIN_SKILL_OVERRIDES is catalog.BUILTIN_SKILL_OVERRIDES
    for name in vars(catalog):
        if name.startswith("AVATAR_ENCRYPTION_"):
            assert getattr(gateway, name) is getattr(catalog, name)


def test_avatar_visual_feedback_and_author_material_guidance_are_scoped():
    import agent_builtin_skill_catalog as catalog

    by_name = {item["name"]: item for item in catalog.BUILTIN_SKILL_GROUPS}
    face = by_name["face-tuning-workflow"]
    assert "vrcforge_call_user" in face["allowedTools"]
    assert "only an explicit pass or fail" in face["instructions"]
    assert "awaiting GM confirmation" in face["instructions"]

    material = by_name["shader-material-workflow"]
    assert {
        "vrcforge_list_directory", "vrcforge_find_files", "vrcforge_read_text_file"
    } <= set(material["allowedTools"])
    assert "body or clothing" in material["instructions"]
    assert "simple color or" in material["instructions"]


def test_author_material_workflow_can_read_product_instructions_and_source_pixels():
    from agent_builtin_skill_catalog import BUILTIN_SKILL_GROUPS

    material = next(item for item in BUILTIN_SKILL_GROUPS if item['name'] == 'shader-material-workflow')
    assert {'vrcforge_web_search', 'vrcforge_web_fetch', 'vrcforge_read_local_image'} <= set(material['allowedTools'])
    assert 'BOOTH' in material['instructions']
    assert 'not evidence of having read' in material['instructions']
    assert 'base, overlay, decal, alpha, or mask' in material['instructions']
