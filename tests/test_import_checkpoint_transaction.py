"""Exercise registry checkpoint creation, owned copy, witness and exact restore."""
from copy import deepcopy
from pathlib import Path
import zipfile

from agent_gateway import AgentGateway
from execution_target import canonical_namespace, project_identity
from prepared_loose_outfit_import import execute_loose_outfit_import
from prepared_unity_execution import prepared_evidence
from test_import_asset_checkpoint_scope import _loose_fixture
from unity_import_checkpoint import prepare_import_asset_checkpoint


def test_approved_import_persists_created_file_witness_and_restores_exact_scope(tmp_path):
    project, _, sealed = _loose_fixture(tmp_path)
    unrelated = project / 'Assets/User.txt'
    unrelated.write_bytes(b'preserve user file')
    (project / 'Assets/Main.unity').write_bytes(b'saved scene')
    target = {
        'schema': 'vrcforge.execution_target.v1', 'scope': 'avatar',
        'project': {'root': str(project), 'projectId': project_identity(str(project))},
        'editor': {'unityPid': 10, 'processStartTime': '1', 'coreInstanceId': 'core'},
        'scene': {'assetPath': 'Assets/Main.unity', 'absolutePath': str(project/'Assets/Main.unity'),
                  'guid': 'a'*32, 'revision': '1', 'digest': 'b'*64},
        'avatar': {'globalObjectId': 'avatar-id', 'exactHierarchyPath': 'Avatar', 'name': 'Avatar'},
        'ambiguous': False, 'resolutionCandidateCount': 1,
    }
    target['namespace'] = canonical_namespace(target)
    gateway = AgentGateway(tmp_path/'app/config.json', tmp_path/'app/audit')
    service = gateway.approval_transactions
    inspected = []

    def prepare(root, args):
        return prepare_import_asset_checkpoint(
            root, args, verify_project=lambda x: Path(x['projectPath']),
            prepare=lambda root, **kw: inspected.append(kw['checkpoint_files']) or {'ok': True},
        )

    def write(args):
        result = execute_loose_outfit_import(prepared_evidence(args)['loosePlan'])
        # Model the AssetDatabase's generated metadata in the approved domain.
        for file in result['copiedFiles']:
            (project/(file+'.meta')).write_text('generated meta')
        for folder in ('Assets/Imports', 'Assets/Imports/New', 'Assets/Imports/New/Textures'):
            (project/(folder+'.meta')).write_text('folder meta')
        return {'schema': 'vrcforge.test_import_receipt.v1', **result,
                'verified': True, 'readback': {'files': result['copiedFiles']}}

    service.register_write_handler(
        'vrcforge_import_outfit_package', 'test owned import', 'medium', write,
        request_preparer=lambda args, preview: ({**deepcopy(sealed), **args}, preview),
        checkpoint_prepare_handler=prepare,
    )
    created = service.create_apply_request({
        'target_tool': 'vrcforge_import_outfit_package', 'requires_explicit_approval': True,
        'arguments': {'projectPath': str(project), 'executionTarget': target,
                      'archiveFiles': ['Assets/User.txt']},
    })
    approval = created['approval']['id']
    service.approve(approval)
    result = service.apply_approved({'approval_id': approval})
    assert result['ok'] is True, result
    checkpoint = result['checkpoint']
    assert checkpoint['checkpointScope']['kind'] == 'unity_project_asset_footprint'
    assert checkpoint['archiveFiles'] == []
    assert inspected and 'Assets/User.txt' not in inspected[0]
    assert checkpoint['createdAssetPostState']
    assert all(checkpoint['createdAssetPostState'].values())
    with zipfile.ZipFile(checkpoint['archivePath']) as archive:
        assert archive.namelist() == []
    persisted = gateway.checkpoint_recovery._load_checkpoint(checkpoint['id'])
    assert persisted['createdAssetPostState'] == checkpoint['createdAssetPostState']
    restored = gateway.checkpoint_recovery._restore_archive_checkpoint(persisted)
    assert restored['ok'] is True, restored
    assert not (project/'Assets/Imports').exists()
    assert unrelated.read_bytes() == b'preserve user file'
