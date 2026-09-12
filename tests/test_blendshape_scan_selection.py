"""Public schema and registered-handler regression for exact blendshape reads."""
from types import SimpleNamespace
import jsonschema
import pytest
import dashboard_server as server
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS

TOOL='vrcforge_scan_blendshapes'
PATH='Scene/Avatar'

@pytest.fixture
def scan(monkeypatch):
    renderers=[{'rendererPath':f'{PATH}/{name}','rendererName':name,'meshName':f'{name}Mesh',
        'blendshapes':[{'name':shape,'index':i,'currentWeight':weight,'normalizedWeight':weight/100} for i,(shape,weight) in enumerate(shapes)]}
        for name,shapes in [('Face',[('Smile',12)]),('Body',[('HideChest',100),('Breast',42)]),('bra',[('Fit',67)]),('Dress',[('Hem',8)])]]
    export={'generatedAtUtc':'fixture-time','summary':{'rendererCount':4,'blendshapeCount':5},
        'avatars':[{'avatarPath':PATH,'avatarName':'Avatar','renderers':renderers}]}
    selected=SimpleNamespace(avatar_name='Avatar',avatar_path=PATH)
    monkeypatch.setattr(server,'load_dashboard_settings',lambda request:SimpleNamespace())
    monkeypatch.setattr(server,'load_dashboard_export_payload',lambda settings,request:(export,'unit-test',False))
    monkeypatch.setattr(server,'resolve_avatar_selection',lambda payload,avatar:selected)
    monkeypatch.setattr(server,'remember_loaded_avatar',lambda *args:None)
    monkeypatch.setattr(server,'serialize_selected_avatar',lambda avatar:{'avatarPath':PATH,'rendererCount':4,'blendshapeCount':5})
    monkeypatch.setattr(server,'is_face_related_blendshape',lambda renderer,shape:renderer['rendererName']=='Face')
    def invoke(**params):
        jsonschema.validate(params,UNITY_READ_TOOL_INPUT_SCHEMAS[TOOL])
        return server.AGENT_GATEWAY._tools[TOOL].handler(params)
    return invoke,export

def test_public_all_scope_and_exact_multiple_renderers(scan):
    invoke,export=scan
    result=invoke(avatarPath=PATH,scope='all',rendererPaths=[f'{PATH}/Body',f'{PATH}/bra',f'{PATH}/Dress'])
    assert result['filterScope']=='all'
    assert [(row['rendererName'],row['blendshapeName'],row['currentWeight']) for row in result['blendshapes']]==[
        ('Body','HideChest',100),('Body','Breast',42),('bra','Fit',67),('Dress','Hem',8)]
    assert all(row['rendererPath']==f"{PATH}/{row['rendererName']}" for row in result['blendshapes'])
    assert result['rendererPaths']==[f'{PATH}/Body',f'{PATH}/bra',f'{PATH}/Dress']
    assert [row['rendererName'] for row in result['avatars'][0]['renderers']]==['Body','bra','Dress']
    assert result['summary']=={'avatarCount':1,'rendererCount':3,'blendshapeCount':4}
    assert result['sourceSummary']=={'rendererCount':4,'blendshapeCount':5}
    assert result['summaryScope']=='selection_inventory'
    assert result['selectedAvatar']['scope']=='renderer_selection_inventory'
    assert result['selectedAvatar']['rendererCount']==3
    assert result['selectedAvatar']['blendshapeCount']==4
    assert result['sourceSelectedAvatar']['scope']=='avatar_inventory'
    assert result['sourceSelectedAvatar']['rendererCount']==4
    assert result['sourceSelectedAvatar']['blendshapeCount']==5
    assert len(export['avatars'][0]['renderers'])==4, 'Do not mutate cached export'

def test_default_face_preserves_original_inventory(scan):
    invoke,export=scan
    result=invoke(avatarPath=PATH)
    assert result['filterScope']=='face'
    assert [row['blendshapeName'] for row in result['blendshapes']]==['Smile']
    assert result['avatars']==export['avatars']

def test_all_without_renderer_filter_returns_every_shape(scan):
    result=scan[0](scope='all')
    assert len(result['blendshapes'])==5

def test_exact_selection_retains_explicit_face_filter(scan):
    result=scan[0](scope='face',rendererPaths=[f'{PATH}/Body'])
    assert result['blendshapes']==[]
    assert result['summary']=={'avatarCount':1,'rendererCount':1,'blendshapeCount':2}
    assert result['summaryScope']=='selection_inventory'
    assert result['filterNote']=='Only face-related blendshapes are shown for the face editor.'

def test_unknown_renderer_is_rejected_not_broadened(scan):
    with pytest.raises(Exception,match='Unknown rendererPaths'):
        scan[0](scope='all',rendererPaths=['Body'])

@pytest.mark.parametrize('params',[{'scope':'body'},{'rendererPaths':[]},{'rendererPaths':['x','x']},
    {'rendererPaths':['']},{'rendererPaths':[' ']},{'rendererPaths':[42]},
    {'rendererPaths':[str(i) for i in range(33)]},{'filterScope':'all'}])
def test_public_invalid_selection_rejected(params):
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(params,UNITY_READ_TOOL_INPUT_SCHEMAS[TOOL])
