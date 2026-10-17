from __future__ import annotations

import dashboard_server


CHECKPOINT_TOOLS = ("vrcforge_select_scene_object",)
RUNTIME_NO_CHECKPOINT_TOOLS = (
    "vrcforge_gesture_manager_enter_play_mode",
    "vrcforge_gesture_manager_set_parameter",
    "vrcforge_set_play_mode",
    "vrcforge_confirm_unity_reload_dialog",
)

def test_editor_state_handlers_use_bound_checkpoint_policy() -> None:
    handlers = dashboard_server.AGENT_GATEWAY._write_handlers
    assert handlers["vrcforge_set_property"].pre_write_checkpoint_required is True
    for name in CHECKPOINT_TOOLS:
        handler = handlers[name]
        assert handler.requires_approved_execution_context is True
        assert handler.pre_write_checkpoint_required is True
        assert handler.checkpoint_prepare_handler is not None
    for name in RUNTIME_NO_CHECKPOINT_TOOLS:
        handler = handlers[name]
        if name != "vrcforge_confirm_unity_reload_dialog":
            assert handler.requires_approved_execution_context is True
        assert handler.pre_write_checkpoint_required is False or callable(handler.pre_write_checkpoint_required)
        assert handler.checkpoint_prepare_handler is not None or name == "vrcforge_confirm_unity_reload_dialog"


def test_play_transitions_do_not_claim_checkpoint_rollback() -> None:
    gm_handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_gesture_manager_enter_play_mode"]
    assert gm_handler.pre_write_checkpoint_required is False
    assert "Project Play callbacks may save or generate assets" in gm_handler.description
    policy = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_set_play_mode"].pre_write_checkpoint_required
    assert policy is False
    assert "Project Play callbacks may save or generate assets" in (
        dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_set_play_mode"].description
    )
