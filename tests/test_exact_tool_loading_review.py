"""Independent regression review of exact-tool session lifecycle."""
import threading

from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts


def test_subset_load_does_not_shrink_a_previously_whole_loaded_block():
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock()))
    block = "behavior/animator_clips_bindings"
    state.load_internal_tool_block("review", block)
    assert state.internal_tool_selections("review")[block] is None
    state.load_internal_tool_block_selected("review", block, ["unity_scan_fx_animator"])
    assert state.internal_tool_selections("review")[block] is None
