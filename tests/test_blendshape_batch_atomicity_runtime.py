"""Actual public preparer and compiled Core body; no live Unity writes."""
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
import dashboard_server as dashboard
from prepared_unity_execution import prepared_call
from test_avatar_tuning_state_service import _prepared_service

ROOT = Path(__file__).resolve().parents[1]


def run_core(tmp_path, request, main):
    path = ROOT / 'tests/test_blendshape_scene_save_runtime.py'
    source = path.read_text(encoding='utf-8')
    revision = os.environ.get('VRCFORGE_BLENDSHAPE_GIT_REF')
    if revision:
        old = subprocess.check_output(['git', 'show', revision + ':Assets/VRCForge/Editor/BlendshapeApplier.cs'], cwd=ROOT, text=True, encoding='utf-8')
        source = source.replace('s=(ROOT/"Assets/VRCForge/Editor/BlendshapeApplier.cs").read_text(encoding="utf-8")', 's=' + repr(old))
    source = source.replace('public int GetBlendShapeIndex(string s)=>0;', 'public int GetBlendShapeIndex(string s)=>s=="Missing"?-1:0;')
    start = source.index('static int Main(){')
    end = source.index('\n"""', start)
    literal = json.dumps(request).replace('"', '""')
    source = source[:start] + 'static int Main(){var request=JObject.Parse(@"' + literal + '");' + main + 'return 0;}}\n' + source[end:]
    scope = {'__file__': str(path)}
    exec(compile(source, str(path), 'exec'), scope)
    scope['test_blendshape_flushes_before_scoped_save_and_checks_result'](tmp_path)


@pytest.mark.parametrize('source_mode', ['configured_export', 'custom_export'])
def test_public_stale_export_prevalidates_entire_core_batch(tmp_path, monkeypatch, source_mode):
    service, stores, undo, _ = _prepared_service(tmp_path)
    export_path = tmp_path / 'export.json'
    export_path.write_text(json.dumps({'avatars': [{'avatarName': 'Avatar', 'avatarPath': 'Avatar',
        'sceneName': 'Test', 'renderers': [{'rendererPath': 'Avatar/Face', 'blendshapes': [
            {'name': 'Smile', 'currentWeight': 100}, {'name': 'Missing', 'currentWeight': 0}]}]}]}), encoding='utf-8')
    monkeypatch.setattr(dashboard, 'load_dashboard_settings', lambda _: SimpleNamespace(export_path=export_path))
    monkeypatch.setattr(dashboard, 'AVATAR_TUNING_STORES', stores)
    monkeypatch.setattr(dashboard, 'remember_loaded_avatar', lambda *_: None)
    registered = dashboard.AGENT_GATEWAY.approval_transactions._ports.state.write_handlers['vrcforge_apply_blendshapes']
    owner = registered.handler.__self__
    monkeypatch.setattr(owner, '_stores', stores)
    monkeypatch.setattr(owner, '_undo', undo)
    monkeypatch.setattr(owner, '_ports', replace(owner._ports, remember_avatar=lambda *_: None))
    prepared, _ = registered.request_preparer({'avatar': 'Avatar', 'source_mode': source_mode,
        'mock_execute': False, 'export_json': str(export_path), 'adjustments': [
            {'renderer_path': 'Avatar/Face', 'blendshape_name': 'Smile', 'target_weight': 55},
            {'renderer_path': 'Avatar/Face', 'blendshape_name': 'Missing', 'target_weight': 70}]}, None)
    tool, request = prepared_call(prepared)
    assert tool == 'vrc_apply_blendshapes'
    assert len(request['adjustments']) == 2
    run_core(tmp_path, request, '''var result=(VRCForgeToolResult)HandleCommand(request);
if(result.ok || Target.weight!=100 || Target.gameObject.scene.isDirty)
 throw new Exception("late invalid row left partial mutation");''')


@pytest.mark.parametrize('fault', ['write', 'save', 'recovery', 'memory', 'dirty'])
def test_core_failure_requires_verified_disk_memory_and_dirty_recovery(tmp_path, fault):
    flags = {'write': '', 'save': '', 'recovery': 'Fail=true;', 'memory': 'MemoryMismatch=true;', 'dirty': 'DirtyMismatch=true;'}
    setup = 'WriteAnimationCurveTool.AssetEditRecovery.' + flags[fault] if flags[fault] else ''
    setup += 'Renderer.ThrowAfterWrite=true;' if fault == 'write' else ''
    expected = 'rolled_back' if fault in ('save', 'write') else 'unknown'
    run_core(tmp_path, {'adjustments': [{'rendererPath': 'Avatar/Face', 'blendshapeName': 'Smile', 'targetWeight': 55}]}, setup + '''
EditorSceneManager.Fail=true;var result=(VRCForgeToolResult)HandleCommand(request);
if(result.ok || WriteAnimationCurveTool.AssetEditRecovery.Restores!=1)throw new Exception("failed save skipped recovery");
var receipt=JObject.FromObject(result.payload);
if((string)receipt["commitState"]!="''' + expected + '''")throw new Exception("incorrect recovery claim");
if("''' + expected + '''"=="unknown" && receipt["committed"].Type!=JTokenType.Null)
 throw new Exception("uncertain mutation incorrectly claims not committed");
if("''' + expected + '''"=="rolled_back" && (bool)receipt["committed"])
 throw new Exception("restored mutation claims committed");
if((bool)receipt["checkpointRecoveryRequired"]!=''' + str(fault not in ('save', 'write')).lower() + ''')throw new Exception("incorrect checkpoint recovery flag");''')


def test_core_retains_duplicate_order_and_unsaved_semantics(tmp_path):
    run_core(tmp_path, {'saveAssets': False, 'adjustments': [
        {'rendererPath': 'Avatar/Face', 'blendshapeName': 'Smile', 'targetWeight': 55},
        {'rendererPath': 'Avatar/Face', 'blendshapeName': 'Smile', 'targetWeight': 70}]}, '''
Target.gameObject.scene.isDirty=true;
var result=(VRCForgeToolResult)HandleCommand(request);var receipt=JObject.FromObject(result.payload);
if(!result.ok || Target.weight!=70 || (float)receipt["applied"][1]["previousWeight"]!=55)
 throw new Exception("duplicate request order changed");
if((bool)receipt["saved"] || !(bool)receipt["pending"] || WriteAnimationCurveTool.AssetEditRecovery.Captures!=0)
 throw new Exception("unsaved request persisted");''')


def test_core_refuses_dirty_scene_without_save_or_mutation(tmp_path):
    run_core(tmp_path, {'adjustments': [{'rendererPath': 'Avatar/Face', 'blendshapeName': 'Smile', 'targetWeight': 55}]}, '''
Target.gameObject.scene.isDirty=true;var result=(VRCForgeToolResult)HandleCommand(request);
if(result.ok || Target.weight!=100 || !Target.gameObject.scene.isDirty)
 throw new Exception("dirty baseline was overwritten");''')


def test_actual_renderer_resolver_rejects_ambiguous_matches(tmp_path):
    from test_curve_fx_authoring_runtime_contract import method
    from test_constraint_conversion_scope_runtime import run
    revision = os.environ.get('VRCFORGE_BLENDSHAPE_GIT_REF')
    source = subprocess.check_output(['git', 'show', revision + ':Assets/VRCForge/Editor/BlendshapeApplier.cs'], cwd=ROOT, text=True, encoding='utf-8') if revision else (ROOT/'Assets/VRCForge/Editor/BlendshapeApplier.cs').read_text(encoding='utf-8')
    body = method(source, 'private static SkinnedMeshRenderer ResolveRenderer(')
    run(tmp_path, '''using System;using System.Linq;
class SkinnedMeshRenderer {public string transform="Avatar/Face";}
class Resources {public static SkinnedMeshRenderer[] items;public static T[] FindObjectsOfTypeAll<T>()=>items as T[];}
class Probe {static bool IsSceneObject(SkinnedMeshRenderer r)=>true;static string NormalizePath(string s)=>s;
static string GetTransformPath(string t)=>t;static string FindAvatarRoot(string t)=>"Avatar";
''' + body + '''
static int Main(){var first=new SkinnedMeshRenderer();Resources.items=new[]{first};
if(ResolveRenderer("Avatar","Avatar/Face")!=first)throw new Exception("unique lookup changed");
Resources.items=new[]{first,new SkinnedMeshRenderer()};bool rejected=false;
try{ResolveRenderer("Avatar","Avatar/Face");}catch(InvalidOperationException){rejected=true;}
if(!rejected)throw new Exception("ambiguous renderer silently selected");return 0;}}
''')
