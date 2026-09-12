import base64
import hashlib
import io
from pathlib import Path
from unittest.mock import Mock
import jsonschema
import pytest
from PIL import Image
import dashboard_server as server
from mcp_resource_registry import McpResourceRegistry, McpResourceError
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS

GUID='a'*32
ASSET='Assets/Textures/noise.png'

@pytest.fixture
def preview_env(tmp_path,monkeypatch):
    project=tmp_path/'project';path=project/ASSET;path.parent.mkdir(parents=True)
    Image.new('RGBA',(64,32),(24,120,230,255)).save(path)
    Path(str(path)+'.meta').write_text(f'fileFormatVersion: 2\nguid: {GUID}\n')
    core={'ok':True,'assetPath':ASSET,'guid':GUID,'assetType':'UnityEngine.Texture2D','dependencyHash':'b'*32}
    registry=McpResourceRegistry(tmp_path/'registry')
    invocation=Mock(side_effect=lambda *args,**kwargs:dict(core))
    monkeypatch.setattr(server,'load_dashboard_settings',lambda request:object())
    monkeypatch.setattr(server,'invoke_unity_mcp',invocation)
    monkeypatch.setattr(server,'extract_tool_result_payload',lambda value:value)
    monkeypatch.setattr(server.AGENT_GATEWAY,'_mcp_resources',registry)
    return project,path,core,registry,invocation

def test_public_preview_schema_and_project_requirement():
    schema=UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_get_asset_info']
    jsonschema.validate({'projectPath':'D:/Project','assetPath':ASSET,'includePreview':True,'previewMaxSize':32},schema)
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate({'assetPath':ASSET,'includePreview':True},schema)

def test_default_asset_info_is_unchanged(preview_env):
    project,path,core,registry,invoke=preview_env
    assert server.get_asset_info_sync({'projectPath':str(project),'assetPath':ASSET})==core
    assert registry.generation==0

def test_preview_is_bounded_image_resource_with_source_identity(preview_env):
    project,path,core,registry,invoke=preview_env
    before=path.read_bytes()
    result=server.get_asset_info_sync({'projectPath':str(project),'assetPath':ASSET,'includePreview':True,'previewMaxSize':32})
    preview=result['preview'];assert preview['width']==32 and preview['height']==16
    assert preview['sourceSha256']==hashlib.sha256(before).hexdigest() and preview['assetGuid']==GUID
    assert preview['sourceWidth']==64 and preview['sourceHeight']==32
    assert 'source' in preview['interpretation'].lower()
    assert invoke.call_args.args[2]=={'assetPath':ASSET,'guid':''}, 'Preview fields never reach Core'
    resource=registry.read(preview['resourceUri']);assert 'structuredContent' not in resource
    content=resource['contents'][0];assert content['mimeType']=='image/png' and 'text' not in content
    png=base64.b64decode(content['blob']);assert hashlib.sha256(png).hexdigest()==preview['sha256']
    with Image.open(io.BytesIO(png)) as image:assert image.size==(32,16) and image.getpixel((0,0))==(24,120,230,255)
    assert path.read_bytes()==before
    assert registry.list()['resources'][0]['mimeType']=='image/png'
    path.unlink()  # synthetic fixture only: immutable resource reads must not revisit source.
    reloaded=McpResourceRegistry(registry.store_dir)
    assert reloaded.read(preview['resourceUri'])==resource

@pytest.mark.parametrize('kind',['failed-core','wrong-path','wrong-guid','wrong-meta','wrong-type','missing-project','traversal','wrong-size'])
def test_preview_rejects_unverified_source_and_publishes_nothing(preview_env,kind):
    project,path,core,registry,invoke=preview_env
    params={'projectPath':str(project),'assetPath':ASSET,'includePreview':True,'previewMaxSize':32}
    if kind=='failed-core':core['ok']=False
    elif kind=='wrong-path':core['assetPath']='Assets/Textures/other.png'
    elif kind=='wrong-guid':params['guid']='c'*32
    elif kind=='wrong-meta':Path(str(path)+'.meta').write_text('guid: '+'d'*32)
    elif kind=='wrong-type':core['assetType']='UnityEngine.Material'
    elif kind=='missing-project':params.pop('projectPath')
    elif kind=='traversal':params['assetPath']=core['assetPath']='Assets/Textures/../Textures/noise.png'
    elif kind=='wrong-size':params['previewMaxSize']=False
    with pytest.raises(ValueError):server.get_asset_info_sync(params)
    assert registry.generation==0

def test_unknown_preview_uri_does_not_read_arbitrary_file(preview_env):
    with pytest.raises(McpResourceError):preview_env[3].read('vrcforge://texture-preview/not-published?revision=1')

@pytest.mark.parametrize('kind',['corrupt','byte-limit','pixel-limit'])
def test_source_decode_and_size_failures_publish_nothing(preview_env,monkeypatch,kind):
    import texture_preview_resources as module
    project,path,core,registry,invoke=preview_env
    if kind=='corrupt':path.write_bytes(b'not an image')
    elif kind=='byte-limit':monkeypatch.setattr(module,'MAX_SOURCE_BYTES',1)
    else:monkeypatch.setattr(module,'MAX_SOURCE_PIXELS',1)
    with pytest.raises(ValueError):server.get_asset_info_sync({'projectPath':str(project),'assetPath':ASSET,'includePreview':True})
    assert registry.generation==0

def test_preview_revision_changes_only_with_captured_data(preview_env):
    project,path,core,registry,invoke=preview_env
    params={'projectPath':str(project),'assetPath':ASSET,'includePreview':True}
    first=server.get_asset_info_sync(params)['preview']
    assert server.get_asset_info_sync(params)['preview']==first
    historical=registry.read(first['resourceUri'])
    Image.new('RGBA',(64,32),(255,0,0,255)).save(path)
    second=server.get_asset_info_sync(params)['preview']
    assert second['revision']==first['revision']+1
    assert second['sourceSha256']!=first['sourceSha256']
    assert registry.read(first['resourceUri'])==historical
