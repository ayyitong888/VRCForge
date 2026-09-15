"""Validation source summaries must preserve returned scanner errors."""
import pytest
import dashboard_server as dashboard
from optimization_service import build_optimization_tool_result

@pytest.mark.parametrize('error', ['Avatar descriptor not found', {'code':'avatar_missing','message':'Avatar descriptor not found'}])
def test_returned_failure_survives_validation_summary(error):
    source=dashboard._run_validation_source('parameters',lambda:{'ok':False,'error':error})
    summary={'ok':bool(source.get('ok')),'error':source.get('error')}
    result=build_optimization_tool_result('optimization.parameter.inventory',{}, {'sources':{'parameters':summary}})
    assert result['ok'] is False
    assert 'Avatar descriptor not found' in result['error']['message']


def test_successful_source_remains_successful():
    result=dashboard._run_validation_source('parameters',lambda:{'ok':True,'parameters':[]})
    assert result['ok'] and result['payload']['parameters']==[]
    assert 'error' not in result
