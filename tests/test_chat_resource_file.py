from pathlib import Path
import pytest
from chat_resource_file import authorize_chat_resource_file


def fixture(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    artifacts = tmp_path / 'artifacts'; artifacts.mkdir()
    target = project / 'result.json'; target.write_text('{}')
    chat = {'sessionId':'s','projectPath':str(project),'items':[{'type':'agent','response':{
        'sessionId':'s','clientTurnId':'t','steps':[{'result':{'outputPath':str(target)}}]}}]}
    return dict(path=str(target),session_id='s',client_turn_id='t',project_path=str(project),chat=chat,artifact_root=artifacts)


def test_durable_tool_file_and_exact_markdown_link(tmp_path):
    args=fixture(tmp_path)
    assert authorize_chat_resource_file(**args)['path']==str(Path(args['path']).resolve())
    response=args['chat']['items'][0]['response']
    response['plan']={'reply':f"[file](<{args['path']}>)"};response['steps']=[]
    assert authorize_chat_resource_file(**args)['path']==str(Path(args['path']).resolve())
    response['plan']['reply']=str(args['path'])
    with pytest.raises(ValueError):authorize_chat_resource_file(**args)


def test_wrong_turn_and_outside_project_denied(tmp_path):
    args=fixture(tmp_path)
    with pytest.raises(ValueError):authorize_chat_resource_file(**{**args,'client_turn_id':'different'})
    outside=tmp_path/'outside.json';outside.write_text('{}')
    args['path']=str(outside);args['chat']['items'][0]['response']['steps'][0]['result']['outputPath']=str(outside)
    with pytest.raises(ValueError):authorize_chat_resource_file(**args)


def test_projectless_owned_artifact_allowed(tmp_path):
    args=fixture(tmp_path);target=args['artifact_root']/'report.txt';target.write_text('done')
    args.update(path=str(target),project_path='');args['chat']['projectPath']=''
    args['chat']['items'][0]['response']['steps'][0]['result']['outputPath']=str(target)
    assert authorize_chat_resource_file(**args)['path']==str(target.resolve())


def test_streaming_tool_result_works_without_final_response(tmp_path):
    args=fixture(tmp_path)
    args['chat']['items']=[{'type':'streaming','clientTurnId':'t','timeline':[{'kind':'tool_result','payload':{'path':args['path']}}]}]
    assert authorize_chat_resource_file(**args)['path']==str(Path(args['path']).resolve())


def test_markdown_file_with_spaces_line_suffix_and_code_denied(tmp_path):
    args=fixture(tmp_path);target=Path(args['project_path'])/'report (final).txt';target.write_text('done')
    args['path']=str(target)
    response=args['chat']['items'][0]['response'];response['steps']=[]
    response['plan']={'reply':f"已完成：[查看报告](<{target}:12>)"}
    assert authorize_chat_resource_file(**args)['path']==str(target.resolve())
    for text in [f"`[查看报告](<{target}>)`",f"```md\n[查看报告](<{target}>)\n```",f"这里只提到了 {target}"]:
        response['plan']['reply']=text
        with pytest.raises(ValueError):authorize_chat_resource_file(**args)
