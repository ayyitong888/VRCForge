import copy
import json
import os
from pathlib import Path
from unittest.mock import Mock
import jsonschema
import pytest
import dashboard_server as server
from approved_unity_execution import freeze_approved_unity_execution_plan
from unity_execution_plans_scene import build_scene_execution_plan
from unity_shared_input_schemas import ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA as SCHEMA

TARGET='vrcforge_write_animation_curve'
ROOT=Path(__file__).resolve().parents[1]
ARCHIVE_ROOT=Path(os.environ['VRCFORGE_REGRESSION_ARCHIVE_ROOT']) if os.environ.get('VRCFORGE_REGRESSION_ARCHIVE_ROOT') else ROOT/'.tmp'/'regression-archives'
ROW={'bindingPath':'Body','componentType':'SkinnedMeshRenderer','propertyName':'blendShape.Fit','constantFloat':42,'overwriteExisting':True}
BASE={'projectPath':'D:/Project','curveSets':[[ROW]],'clips':[{'clipPath':'Assets/A.anim','curveSet':0},{'clipPath':'Assets/B.anim','curveSet':0}]}

def inline(params):
    result=copy.deepcopy(params);sets=result.pop('curveSets')
    for clip in result['clips']:
        if 'curveSet' in clip:clip['curves']=copy.deepcopy(sets[clip.pop('curveSet')])
    return result

def test_public_schema_accepts_shared_and_original_inline():
    jsonschema.validate(BASE,SCHEMA)
    jsonschema.validate(inline(BASE),SCHEMA)

@pytest.mark.parametrize('preview',[True,False])
def test_handler_and_approved_plan_expand_to_same_exact_call(monkeypatch,preview):
    params=copy.deepcopy(BASE);before=copy.deepcopy(params)
    invocation=Mock(return_value={'ok':True,'preview':preview,'batch':True})
    monkeypatch.setattr(server,'load_dashboard_settings',lambda request:object())
    monkeypatch.setattr(server,'invoke_unity_mcp',invocation)
    monkeypatch.setattr(server,'extract_tool_result_payload',lambda payload:payload)
    result=server.write_animation_curve_sync(params,preview=preview)
    expected=server._avatar_primitive_request(inline(BASE),preview=preview)
    assert invocation.call_args.args[1:] == ('vrc_write_animation_curve',expected)
    assert 'curveSets' not in expected and all('curveSet' not in row for row in expected['clips'])
    assert params==before and result=={'ok':True,'preview':preview,'batch':True}, 'Do not echo expanded inputs into receipt'
    shared_plan=build_scene_execution_plan(TARGET,params)
    original_plan=build_scene_execution_plan(TARGET,inline(params))
    assert shared_plan==original_plan
    assert freeze_approved_unity_execution_plan(shared_plan)==freeze_approved_unity_execution_plan(original_plan)
    changed=copy.deepcopy(params);changed['curveSets'][0][0]['constantFloat']=43
    assert freeze_approved_unity_execution_plan(build_scene_execution_plan(TARGET,changed))!=freeze_approved_unity_execution_plan(shared_plan)

def invalid_cases():
    for ref in [-1,1,True,'',None]:
        value=copy.deepcopy(BASE);value['clips'][0]['curveSet']=ref;yield value
    value=copy.deepcopy(BASE);value['clips'][0]['curves']=[ROW];yield value
    value=copy.deepcopy(BASE);value['curveSets']=[];yield value
    value=copy.deepcopy(BASE);value['curveSets']=[[]];yield value
    value=copy.deepcopy(BASE);del value['curveSets'];yield value
    value=copy.deepcopy(BASE);value['clipPath']='Assets/C.anim';yield value

@pytest.mark.parametrize('params',list(invalid_cases()))
def test_invalid_shared_inputs_fail_before_transport_and_plan(monkeypatch,params):
    invocation=Mock();monkeypatch.setattr(server,'invoke_unity_mcp',invocation)
    with pytest.raises((ValueError,jsonschema.ValidationError)):
        server.write_animation_curve_sync(params,preview=True)
    invocation.assert_not_called()
    with pytest.raises((ValueError,jsonschema.ValidationError)):build_scene_execution_plan(TARGET,params)

@pytest.mark.parametrize('kind',['keys','bytes'])
def test_limits_apply_to_expanded_payload(monkeypatch,kind):
    params=copy.deepcopy(BASE)
    if kind=='keys':params['curveSets']=[[{'propertyName':'m_Value','keys':[{'time':i,'value':1} for i in range(2050)]}]]
    else:params['curveSets'][0][0]['bindingPath']='x'*270000
    invocation=Mock();monkeypatch.setattr(server,'invoke_unity_mcp',invocation)
    with pytest.raises(ValueError,match='4096|512 KiB'):server.write_animation_curve_sync(params,preview=True)
    invocation.assert_not_called()

def test_real615_and619_all_batches_expand_exactly():
    folder=ARCHIVE_ROOT
    paths=[folder/'615-preview-batch-request.json']+list(folder.glob('619-write-??-request.json'))
    if not paths[0].exists():pytest.skip('Local public acceptance archives unavailable')
    count=0
    for path in paths:
        for request in json.loads(path.read_text('utf8')):
            if request['params']['name'] not in {TARGET,'vrcforge_preview_write_animation_curve'}:continue
            original=request['params']['arguments'];params=copy.deepcopy(original);sets=[];lookup={}
            for clip in params['clips']:
                key=json.dumps(clip['curves'],sort_keys=True)
                if key not in lookup:lookup[key]=len(sets);sets.append(clip['curves'])
                clip['curveSet']=lookup[key];del clip['curves']
            params['curveSets']=sets
            assert build_scene_execution_plan(TARGET,params)==build_scene_execution_plan(TARGET,original)
            count+=1
    assert count==14
