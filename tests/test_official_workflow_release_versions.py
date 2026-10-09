from pathlib import Path
import json
import runpy

from skill_packages import SkillPackageService


def test_official_release_preserves_each_source_version(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    namespace = runpy.run_path(str(root / 'scripts/prepare_official_workflows.py'))
    prepare = namespace['prepare']
    state = prepare.__globals__
    pair = SkillPackageService.generate_signing_keypair()
    private_path = tmp_path / 'test-private.pem'
    public_path = tmp_path / 'test-public.key'
    SkillPackageService.save_signing_keypair(pair, private_path, public_path)
    monkeypatch.setitem(state, 'ARTIFACT_ROOT', tmp_path / 'build')
    monkeypatch.setitem(state, 'PRIVATE_KEY_PATH', private_path)
    monkeypatch.setitem(state, 'PUBLIC_KEY_PATH', public_path)
    monkeypatch.setitem(state, 'signer', lambda: pair)
    result = prepare()
    expected = {package_id: json.loads((source / 'manifest.json').read_text(encoding='utf-8'))['version']
                for package_id, source, _ in state['PACKAGES']}
    assert len(result['packages']) == 9
    assert {row['id']: row['version'] for row in result['packages']} == expected
