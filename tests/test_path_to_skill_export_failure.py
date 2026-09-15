"""Actual Path-to-Skill source publication and filesystem export failure."""
from pathlib import Path
from dataclasses import replace

import pytest

import dashboard_server
from path_to_skill_controller import PathToSkillWriteService


def test_export_parent_failure_does_not_leave_published_source(tmp_path):
    source = tmp_path / 'captured-source'
    blocker = tmp_path / 'not-a-directory'
    blocker.write_bytes(b'keep-existing-file')
    with pytest.raises((OSError, ValueError)):
        dashboard_server.PATH_TO_SKILL_WRITE.write({
            'summary': {'status': 'passed', 'workflow': 'captured_workflow', 'steps': ['captured.read']},
            'outputPath': str(source), 'writeSource': True,
            'exportVsk': True, 'confirmExport': True,
            'packageOutputPath': str(blocker / 'result.vsk'),
        })
    assert not source.exists()
    assert blocker.read_bytes() == b'keep-existing-file'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['not-a-directory']


@pytest.mark.parametrize('change', ['content', 'new_file', 'new_directory'])
def test_export_failure_preserves_source_changed_after_publication(tmp_path, change):
    source = tmp_path / 'source'
    blocker = tmp_path / 'blocker'
    blocker.write_bytes(b'keep')
    ports = dashboard_server.PATH_TO_SKILL_WRITE._ports
    snapshots = {}

    def prepare_parent(path):
        # Scheduling seam only: source writes and filesystem failure are real.
        if change == 'content':
            (source / 'manifest.json').write_bytes(b'user edit')
        elif change == 'new_file':
            (source / 'user.txt').write_bytes(b'user file')
        else:
            (source / 'user-directory').mkdir()
        snapshots.update({str(p.relative_to(source)): p.read_bytes()
                          for p in source.rglob('*') if p.is_file()})
        ports.ensure_parent(path)

    service = PathToSkillWriteService(replace(ports, ensure_parent=prepare_parent))
    with pytest.raises(ValueError, match='source recovery failed'):
        service.write({
            'summary': {'status': 'passed', 'workflow': 'captured_workflow', 'steps': ['captured.read']},
            'outputPath': str(source), 'exportVsk': True, 'confirmExport': True,
            'packageOutputPath': str(blocker / 'result.vsk'),
        })
    assert {str(p.relative_to(source)): p.read_bytes()
            for p in source.rglob('*') if p.is_file()} == snapshots
    if change == 'new_directory':
        assert (source / 'user-directory').is_dir()


@pytest.mark.parametrize('collision', [False, True])
def test_actual_export_success_and_late_destination_collision(tmp_path, collision):
    source, output = tmp_path / 'source', tmp_path / 'result.vsk'
    ports = dashboard_server.PATH_TO_SKILL_WRITE._ports

    def prepare_parent(path):
        ports.ensure_parent(path)
        if collision:
            output.write_bytes(b'other writer')

    service = PathToSkillWriteService(replace(ports, ensure_parent=prepare_parent))
    arguments = {
        'summary': {'status': 'passed', 'workflow': 'captured_workflow', 'steps': ['captured.read']},
        'outputPath': str(source), 'exportVsk': True, 'confirmExport': True,
        'packageOutputPath': str(output),
    }
    if collision:
        with pytest.raises(ValueError, match='generated source was retracted'):
            service.write(arguments)
        assert output.read_bytes() == b'other writer'
        assert not source.exists()
    else:
        result = service.write(arguments)
        assert result['ok'] and output.is_file() and source.is_dir()
        assert dashboard_server.skill_package_service().inspect_package(output).manifest['id'] == result['manifest']['id']
