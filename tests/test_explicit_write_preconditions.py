from copy import deepcopy
import pytest
from agent_gateway import AgentGateway, AgentGatewayError
from test_project_asset_copy import wrapper, preview_payload, missing_folder_preview
from project_asset_copy import bind_authoritative_preview, compute_preview_digest, ProjectAssetCopyError


@pytest.mark.parametrize("location", ["top", "arguments"])
def test_copy_stale_preview_digest_is_never_replaced(location):
 request=wrapper()
 (request if location == "top" else request["arguments"])["expectedPreviewDigest"] = "6a0f7fd10f85c36e558b96ecf2f5b068240e3119c4241dee00bdd5d11a291440"
 with pytest.raises(ProjectAssetCopyError, match="expectedPreviewDigest"):
  bind_authoritative_preview(request, preview_payload())


def test_copy_old_absent_parent_lock_rejects_after_another_copy_created_parent():
 before=missing_folder_preview(); request=wrapper()
 request["arguments"]["destinationAssetPath"]=before["target"]["assetPath"]
 locked,_=bind_authoritative_preview(request,before)
 fresh=deepcopy(before);fresh["target"].pop("folderCreation")
 fresh["target"].update(parentFolderGuid="a"*32,parentFolderIdentity="b"*64)
 fresh["previewDigest"]=compute_preview_digest(fresh)
 with pytest.raises(ProjectAssetCopyError): bind_authoritative_preview(locked,fresh)


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("change", ["replace", "drop", "mutate_nested"])
def test_any_preparer_cannot_replace_or_drop_explicit_preconditions(tmp_path, external, change):
 gateway=AgentGateway(tmp_path/"config.json",tmp_path/"audit")
 name="vrcforge_fixture_write"
 def prepare(arguments,preview):
  if change == "mutate_nested": arguments["arguments"]["expectedDigest"]="fresh"
  elif change == "drop": arguments.pop("expectedDigest")
  else: arguments["expectedDigest"]="fresh"
  return arguments, {"ok":True}
 gateway.approval_transactions.register_write_handler(name,"Fixture", "medium", lambda _:pytest.fail("must not execute"),request_preparer=prepare)
 args={"arguments":{"expectedDigest":"old"}} if change=="mutate_nested" else {"expectedDigest":"old"}
 with pytest.raises(AgentGatewayError, match="expectedDigest"):
  if external: gateway.approval_transactions.prepare_external_mcp_write(name,args)
  else: gateway.approval_transactions.create_apply_request({"target_tool":name,"arguments":args})


@pytest.mark.parametrize("external", [False, True])
def test_outer_lock_cannot_survive_only_as_unused_wrapper_metadata(tmp_path, external):
 gateway=AgentGateway(tmp_path/"config.json",tmp_path/"audit")
 name="vrcforge_fixture_write"
 gateway.approval_transactions.register_write_handler(name,"Fixture","medium",lambda _: {},request_preparer=lambda a,p: ({**a,"arguments":{"value":1}}, {}))
 with pytest.raises(AgentGatewayError, match="expectedDigest"):
  if external: gateway.approval_transactions.prepare_external_mcp_write(name,{"expectedDigest":"old"})
  else: gateway.approval_transactions.create_apply_request({"target_tool":name,"arguments":{"expectedDigest":"old"}})


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("locked", [False, True])
def test_automatic_preview_and_matching_explicit_lock_remain_usable(tmp_path, external, locked):
 gateway=AgentGateway(tmp_path/"config.json",tmp_path/"audit")
 name="vrcforge_fixture_write"
 gateway.approval_transactions.register_write_handler(name,"Fixture","medium",lambda _: {},request_preparer=lambda a,p: ({"arguments":{"expectedDigest":"fresh"}}, {}))
 args={"expectedDigest":"fresh"} if locked else {}
 if external: result=gateway.approval_transactions.prepare_external_mcp_write(name,args)
 else: result=gateway.approval_transactions.create_apply_request({"target_tool":name,"arguments":args})
 assert isinstance(result,dict)


@pytest.mark.parametrize("key,value", [("expectedDestinationAbsent", 1), ("expectedSourceGuid", "unknown"), ("expectedUnknownLock", "x")])
def test_copy_explicit_lock_types_unknown_fields_and_identity_must_match(key,value):
 request=wrapper();request["arguments"][key]=value
 with pytest.raises(ProjectAssetCopyError,match=key): bind_authoritative_preview(request,preview_payload())


def test_copy_matching_partial_lock_and_auto_preview_unchanged():
 request=wrapper();payload=preview_payload();request["expectedPreviewDigest"]=payload["previewDigest"]
 canonical,_=bind_authoritative_preview(request,payload)
 assert canonical["arguments"]["expectedPreviewDigest"]==payload["previewDigest"]
 assert canonical["arguments"]["expectedSourceGuid"]==payload["source"]["guid"]
