"""Bind property checkpoint files to fresh read-only Core owner discovery."""
from pathlib import Path
from typing import Any, Callable


def prepare_component_property_checkpoint(
    project_root: Path,
    arguments: dict[str, Any],
    *,
    build_plan: Callable,
    discover: Callable,
    inspect_owner: Callable,
    prepare: Callable,
) -> dict[str, Any]:
    def rejected(message: str) -> dict[str, Any]:
        return {'ok': False, 'blocking': True, 'code': 'checkpoint_scope_unresolved',
                'error': message, 'mutationStarted': False, 'saved': False}

    target = arguments.get('executionTarget')
    if not isinstance(target, dict) or target.get('scope') != 'component':
        return rejected('The approved property write requires its exact component identity.')
    project = target.get('project') or {}
    scene = target.get('scene') or {}
    obj = target.get('object') or {}
    component = target.get('component') or {}
    calls = build_plan('vrcforge_set_property', arguments)
    request = calls[0][1] if len(calls) == 1 and calls[0][0] == 'vrc_set_property' else {}
    scene_path = scene.get('assetPath')
    if (Path(str(project.get('root') or '')).resolve() != project_root.resolve()
            or not obj.get('globalObjectId') or not component.get('globalObjectId')
            or request.get('gameObjectPath') != obj.get('exactHierarchyPath')
            or not isinstance(scene_path, str) or not scene_path.startswith('Assets/')
            or not scene_path.lower().endswith('.unity') or '\\' in scene_path
            or any(part in {'', '.', '..'} for part in scene_path.split('/'))
            or not scene.get('guid') or not scene.get('digest')):
        return rejected('The approved property write does not identify a saved scene component.')
    query = {'projectPath': str(project_root), 'scope': 'component',
             'objectGlobalObjectId': obj['globalObjectId'],
             'componentGlobalObjectId': component['globalObjectId']}
    if (target.get('avatar') or {}).get('globalObjectId'):
        query['avatarGlobalObjectId'] = target['avatar']['globalObjectId']
    live = discover(query)
    if live.get('ok') is not True or live.get('targets') != [target]:
        return rejected('The approved property target changed before checkpointing.')

    # Only actual read-only Core owner discovery is authoritative. Caller-supplied footprint
    # fields are deliberately ignored, including when the Core is older.
    result = inspect_owner({**arguments, 'includeCheckpointScope': True})
    if result.get('ok') is False or result.get('success') is False:
        return {**result, 'ok': False, 'blocking': True, 'mutationStarted': False, 'saved': False}
    scope = result.get('checkpointScope')
    if result.get('ok') is not True or not isinstance(scope, dict):
        return rejected('The loaded Core did not prove the property persistence owner; load the current Core.')
    expected = {
        'schema': 'vrcforge.component_property_checkpoint.v1', 'ownerKind': 'scene',
        'scenePath': scene_path, 'sceneGuid': scene['guid'], 'sceneDigest': scene['digest'],
        'objectGlobalObjectId': obj['globalObjectId'],
        'componentGlobalObjectId': component['globalObjectId'],
        'componentType': component['type'], 'componentIndex': request['componentIndex'],
        'propertyPath': request['propertyPath'], 'archiveAssetPaths': [scene_path],
    }
    if any(scope.get(key) != value for key, value in expected.items()):
        return rejected('The Core property persistence owner differs from the approved target.')
    prepared = prepare(project_root, checkpoint_files=[scene_path])
    if prepared.get('ok') is not True:
        return prepared
    return {**prepared, 'archiveAssetPaths': [scene_path],
            'archiveScopeReason': 'revalidated_component_property_scene_owner'}
