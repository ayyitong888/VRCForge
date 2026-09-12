"""Finite synthetic registry processes/handles only; no network or Unity."""
import multiprocessing
import os
from pathlib import Path
import threading
import time
import pytest
from mcp_resource_registry import McpResourceRegistry


def publish(registry, key, value):
    return registry.publish(base_uri='vrcforge://test/'+key, name=key,
        resource_type='test', data={'value':value}, source_mode='test', refresh_rule='Explicit publish.')


def _writer(store, value, ready, start, results):
    try:
        registry=McpResourceRegistry(Path(store))
        ready.put(value)
        assert start.wait(5)
        row=publish(registry,'shared',value)
        results.put(('ok',row['revision']))
    except Exception as exc:
        results.put(('error',repr(exc)))


def test_stale_registry_instances_merge_and_allocate_distinct_revisions(tmp_path):
    first=McpResourceRegistry(tmp_path);second=McpResourceRegistry(tmp_path)
    a=publish(first,'shared','a');b=publish(second,'shared','b')
    assert (a['revision'],b['revision'])==(1,2)
    assert first.read(b['uri'])['structuredContent']['data']=={'value':'b'}
    assert second.read(a['uri'])['structuredContent']['data']=={'value':'a'}


def test_real_spawned_writers_retain_both_immutable_revisions(tmp_path):
    ctx=multiprocessing.get_context('spawn');ready=ctx.Queue();results=ctx.Queue();start=ctx.Event()
    children=[ctx.Process(target=_writer,args=(str(tmp_path),value,ready,start,results)) for value in ('a','b')]
    try:
        for child in children:child.start()
        assert {ready.get(timeout=5),ready.get(timeout=5)}=={'a','b'}
        start.set()
        output=[results.get(timeout=8),results.get(timeout=8)]
        assert sorted(output)==[('ok',1),('ok',2)]
        loaded=McpResourceRegistry(tmp_path)
        assert {loaded.read('vrcforge://test/shared?revision='+str(i))['structuredContent']['data']['value'] for i in (1,2)}=={'a','b'}
    finally:
        for child in children:
            child.join(2)
            if child.is_alive():child.terminate();child.join(2)
        ready.close();results.close();ready.join_thread();results.join_thread()


@pytest.mark.skipif(os.name!='nt',reason='Windows open file replacement semantics')
def test_reader_open_handle_cannot_race_atomic_replace(tmp_path,monkeypatch):
    registry=McpResourceRegistry(tmp_path);old=publish(registry,'old',1)
    entered=threading.Event();errors=[];original=Path.read_bytes
    def held_read(path,*args,**kwargs):
        if path==tmp_path/'registry.json' and threading.current_thread().name=='registry-reader':
            with path.open('rb') as handle:
                entered.set();time.sleep(.2)
                return handle.read()
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_bytes',held_read)
    def reader():
        try:McpResourceRegistry(tmp_path).read(old['uri'])
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=reader,name='registry-reader');thread.start()
    try:
        assert entered.wait(2)
        new=publish(registry,'new',2)
    finally:thread.join(3)
    assert not thread.is_alive() and not errors
    disk=McpResourceRegistry(tmp_path)
    assert disk.read(old['uri']) and disk.read(new['uri'])


def test_transient_windows_replace_denial_preserves_one_commit(tmp_path,monkeypatch):
    registry=McpResourceRegistry(tmp_path);old=publish(registry,'shared',1)
    original=Path.replace;attempts=[]
    def denied_once(path,target):
        if Path(target)==tmp_path/'registry.json':
            attempts.append(path)
            if len(attempts)==1:
                exc=PermissionError(13,'synthetic WinError5');exc.winerror=5;raise exc
        return original(path,target)
    monkeypatch.setattr(Path,'replace',denied_once)
    new=publish(registry,'shared',2)
    assert new['revision']==2 and len(attempts)==2
    assert registry.read(old['uri'])['structuredContent']['data']=={'value':1}
    assert not list(tmp_path.glob('*.tmp'))


def test_permanent_replace_failure_rolls_back_and_cleans_owned_temporary(tmp_path,monkeypatch):
    registry=McpResourceRegistry(tmp_path);old=publish(registry,'shared',1)
    before=(tmp_path/'registry.json').read_bytes()
    def denied(path,target):
        exc=PermissionError(13,'synthetic persistent WinError5');exc.winerror=5;raise exc
    monkeypatch.setattr(Path,'replace',denied)
    with pytest.raises(PermissionError):publish(registry,'shared',2)
    assert (tmp_path/'registry.json').read_bytes()==before
    assert registry.read(old['uri'])['structuredContent']['data']=={'value':1}
    assert registry.generation==1 and not list(tmp_path.glob('*.tmp'))
