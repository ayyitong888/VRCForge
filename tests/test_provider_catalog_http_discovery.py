from dataclasses import replace
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
import dashboard_server as server
from provider_model_catalog_service import ProviderModelCatalogService, ProviderModelCatalogPolicyPorts, ProviderModelCatalogSdkPorts
from model_provider_adapters import provider_model_descriptor
from test_reasoning_effort import make_configuration_owner, make_settings

@pytest.mark.parametrize('model', ['deepseek-auto','deepseek-flash',None])
def test_catalog_http_discovers_before_model_selection_without_saving(tmp_path, monkeypatch, model):
    owner=make_configuration_owner(tmp_path,document={'provider_keys':{'api':{'deepseek':'fixture-key'}}},runtime_settings=make_settings('chatgpt','gpt-6.1-sol','high'))
    before=owner.config_path.read_bytes();active=owner.current_api_config();calls=[]
    catalog=ProviderModelCatalogService(ProviderModelCatalogPolicyPorts(
        validate_provider_api_key=lambda key:None,provider_requires_api_key=lambda p:True,
        provider_display_name=lambda p:p,provider_model_descriptor=provider_model_descriptor,
        resolve_vertex_project_location=lambda u:('p','l')),
        ProviderModelCatalogSdkPorts(openai_client=lambda key,url,timeout: calls.append((key,url)) or SimpleNamespace(models=SimpleNamespace(list=lambda:{'data':[
            {'id':'deepseek-flash','effort':{'supported_levels':['low','high','max'],'default_level':'high'}},
            {'id':'deepseek-chat'},
        ]})),google_ai_studio_client=lambda _:None,google_vertex_client=lambda *_:None,anthropic_client=lambda _:None))
    monkeypatch.setattr(server,'PROVIDER_CONFIGURATION',owner);monkeypatch.setattr(server,'PROVIDER_MODEL_CATALOG',catalog)
    monkeypatch.setattr(server,'APP_AUTH_REQUIRED',True);monkeypatch.setattr(server,'APP_SESSION_TOKEN','catalog-http-fixture-token')
    body={'provider':'deepseek','api_key':'','base_url':'https://api.deepseek.com','model':model,'api_type':'auto'}
    with TestClient(server.app,client=('127.0.0.1',1234),raise_server_exceptions=False) as client:
        response=client.post('/api/models',json=body,headers={'Authorization':'Bearer catalog-http-fixture-token'})
        assert response.status_code==200,response.text
        assert response.headers['content-type'].startswith('application/json')
        payload=response.json();assert payload['modelCount']==2
        assert payload['selectedModel']==(model or '')
        assert next(m for m in payload['models'] if m['id']=='deepseek-flash')['reasoningEfforts']==['low','high','max']
        variants=client.post('/api/app/provider/reasoning-variants',json={**body,'model':model or ''},headers={'Authorization':'Bearer catalog-http-fixture-token'})
        assert variants.status_code==200,variants.text
        if model in {'deepseek-auto',None}:assert variants.json()['variants']==[]
        rejected=client.post('/api/config',json={**body,'model':'deepseek-auto'},headers={'Authorization':'Bearer catalog-http-fixture-token'})
        assert rejected.status_code==400 and 'Select a DeepSeek model' in rejected.json()['detail']
    assert calls==[('fixture-key','https://api.deepseek.com')]
    assert owner.current_api_config()==active
    assert owner.config_path.read_bytes()==before
