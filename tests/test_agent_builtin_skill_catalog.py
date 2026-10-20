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
