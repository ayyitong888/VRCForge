"""Replay the advertised completion contract through the real gateway."""
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway


def test_read_completion_instructions_allow_success_without_correction(tmp_path):
    def follow_instructions(request):
        assert "Cite completed actionIds in evidence_action_ids for read-only success" in request["instructions"]
        control = next(tool for tool in request["tools"]
                       if tool["function"]["name"] == "vrcforge_runtime_action")
        description = control["function"]["parameters"]["properties"]["completion_claim"]["properties"]["evidence_action_ids"]["description"]
        assert "Cite completed actionIds for read-only success" in description
        return finish(request)

    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}),
        follow_instructions,
    ])
    result = run(gateway, tmp_path, maxAgenticTurns=2)
    assert len(model.requests) == 2 and len(invoked) == 1
    assert result["plan"]["nextStep"] == "done"
    assert result["plan"]["taskCompletion"]["evidenceActionIds"]
