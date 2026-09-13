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
        results.put(('ok',row['revision'],row['uri']))
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
        assert sorted((item[0],item[1]) for item in output)==[('ok',1),('ok',2)]
        loaded=McpResourceRegistry(tmp_path)
        assert {loaded.read(item[2])['structuredContent']['data']['value'] for item in output}=={'a','b'}
    finally:
        for child in children:
            child.join(2)
            if child.is_alive():child.terminate();child.join(2)
        ready.close();results.close();ready.join_thread();results.join_thread()


def test_reader_transaction_blocks_writer_without_losing_records(tmp_path):
    registry=McpResourceRegistry(tmp_path);old=publish(registry,'old',1)
    entered=threading.Event();release=threading.Event();errors=[]
    def reader():
        try:
            with McpResourceRegistry(tmp_path)._transaction() as connection:
                assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1
                entered.set();assert release.wait(5)
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=reader,name='registry-reader');thread.start()
    assert entered.wait(2)
    results=[]
    def writer():
        try:results.append(publish(registry,'new',2))
        except Exception as exc:errors.append(exc)
    writing=threading.Thread(target=writer);writing.start()
    release.set();thread.join(5);writing.join(5)
    assert not thread.is_alive() and not writing.is_alive() and not errors
    disk=McpResourceRegistry(tmp_path)
    assert disk.read(old['uri']) and disk.read(results[0]['uri'])


def test_sqlite_commit_failure_rolls_back_and_closes_connection(tmp_path,monkeypatch):
    import sqlite3
    import mcp_resource_registry as module
    registry=McpResourceRegistry(tmp_path);old=publish(registry,'shared',1)
    original=sqlite3.connect;connections=[]
    class FailingCommit(sqlite3.Connection):
        closed=False
        def commit(self):
            # Schema preparation is committed before record inserts.
            if self.total_changes:
                raise sqlite3.OperationalError("synthetic disk full")
            return super().commit()
        def close(self):
            self.closed=True
            return super().close()
    def connect(*args,**kwargs):
        value=original(*args,**kwargs,factory=FailingCommit);connections.append(value);return value
    with monkeypatch.context() as patch:
        patch.setattr(module.sqlite3,'connect',connect)
        with pytest.raises(module.McpResourceError,match='no publication was committed'):
            publish(registry,'shared',2)
    assert connections and all(item.closed for item in connections)
    assert registry.generation==1
    assert registry.read(old['uri'])['structuredContent']['data']=={'value':1}
    assert publish(registry,'shared',3)['revision']==2
