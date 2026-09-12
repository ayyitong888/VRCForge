"""Historical JSON index compatibility; pytest owns all temporary local files."""
import json
import pytest

import mcp_resource_registry as candidate


def publish(registry, key, value):
    return registry.publish(base_uri='vrcforge://test/'+key, name=key,
        resource_type='test', data=value, source_mode='test', refresh_rule='Explicit capture')


def test_roundtrip_preserves_numbers_unicode_and_content_hash(tmp_path):
    data = {'name':'原模型😀衣柜', 'large':10**100+31, 'small':5e-324,
            'max':1.7976931348623157e308, 'negativeZero':-0.0,
            'items':[None,True,False,{'nested':'日本語'}]}
    original = publish(candidate.McpResourceRegistry(tmp_path),'mixed',data)
    actual = candidate.McpResourceRegistry(tmp_path).read(original['uri'])['structuredContent']
    assert json.dumps(actual,sort_keys=True,ensure_ascii=False)==json.dumps(original,sort_keys=True,ensure_ascii=False)


def test_deep_existing_index_remains_readable(tmp_path):
    data = 'leaf'
    for _ in range(300):
        data = [data]
    original = publish(candidate.McpResourceRegistry(tmp_path),'deep',data)
    actual = candidate.McpResourceRegistry(tmp_path).read(original['uri'])['structuredContent']
    assert actual == original


@pytest.mark.parametrize('invalid', [b'{"records":', b'{} trailing', b'{"records":{},"latest":[]}', b'\xff'])
def test_invalid_index_never_publishes_partial_data(tmp_path,invalid):
    index = tmp_path/'registry.json'
    index.write_bytes(invalid)
    with pytest.raises(candidate.McpResourceError,match='could not be loaded'):
        publish(candidate.McpResourceRegistry(tmp_path),'new',{'value':1})
    assert index.read_bytes()==invalid
    assert not list(tmp_path.glob('*.tmp'))


def test_first_publish_preserves_old_revision_and_refresh(tmp_path):
    first = publish(candidate.McpResourceRegistry(tmp_path),'same',{'value':1})
    reader = candidate.McpResourceRegistry(tmp_path)
    assert reader.generation==1
    second = publish(candidate.McpResourceRegistry(tmp_path),'same',{'value':2})
    assert (first['revision'],second['revision'])==(1,2)
    assert reader.read(first['uri'])['structuredContent']==first
    assert reader.read(second['uri'])['structuredContent']==second
