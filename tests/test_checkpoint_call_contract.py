"""Explicit checkpoint scope at every direct source call and real FX preflight."""
import ast
from copy import deepcopy
from pathlib import Path
import pytest
import dashboard_server as server

def test_all_source_calls_explicitly_pass_required_checkpoint_files():
    root=Path(__file__).resolve().parents[1]
    files=[*root.glob('*.py'),*root.joinpath('tests').rglob('*.py'),*root.joinpath('tools').rglob('*.py'),*root.joinpath('scripts').rglob('*.py')]
    violations=[]
    for path in files:
        source=path.read_text(encoding='utf-8-sig')
        if 'prepare_unity_checkpoint_sync' not in source:continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node,ast.Call):continue
            name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
            if name=='prepare_unity_checkpoint_sync' and not any(k.arg=='checkpoint_files' for k in node.keywords):
                violations.append(f'{path.relative_to(root)}:{node.lineno}')
    assert violations==[]
    module=ast.parse((root/'dashboard_server.py').read_text(encoding='utf-8'))
    function=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='prepare_unity_checkpoint_sync')
    index=next(i for i,a in enumerate(function.args.kwonlyargs) if a.arg=='checkpoint_files')
    assert function.args.kw_defaults[index] is None

@pytest.mark.parametrize('batch',[False,True])
def test_real_fx_scope_reaches_keyword_only_preflight_without_meta(tmp_path,monkeypatch,batch):
    controller='Assets/Controllers/Temporary.controller'
    edit={'action':'update_state','layerName':'Left Hand','stateName':'Fist','speed':0.75}
    args={'projectPath':str(tmp_path),'controllerPath':controller,'_checkpointTargetTool':'vrcforge_manage_fx_animator',**({'edits':[edit]} if batch else edit)}
    before=deepcopy(args);seen=[]
    def prepare(root,checkpoint_asset_paths=None,*,checkpoint_files):
        seen.append((root,checkpoint_asset_paths,checkpoint_files));return {'ok':True}
    monkeypatch.setattr(server,'prepare_unity_checkpoint_sync',prepare)
    assert server.prepare_authoritative_unity_checkpoint_sync(tmp_path,args)=={'ok':True}
    assert seen==[(tmp_path,None,[controller])]
    assert args==before

def test_unknown_scope_blocks_without_calling_unity(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'prepare_unity_checkpoint_sync',lambda *a,**kw:pytest.fail('unknown scope must never reach Unity'))
    result=server.prepare_authoritative_unity_checkpoint_sync(tmp_path,{'toolName':'vrc_set_gameobject_active','arguments':{}})
    assert result['ok'] is False and result['blocking'] is True and result['code']=='checkpoint_scope_unresolved'

def test_empty_explicit_scope_blocks_before_core_io(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'UnityMcpCoreClient',lambda *a,**kw:pytest.fail('empty scope must never reach Core'))
    result=server.prepare_unity_checkpoint_sync(tmp_path,checkpoint_files=[])
    assert result['ok'] is False and result['code']=='checkpoint_scope_unresolved'
