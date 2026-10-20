"""Minimal receipt owner for planner-port tests; gateway tests use the real owner."""
from copy import deepcopy
from agent_scan_result_policy import is_scan_result_policy_message
from agent_runtime_session_state import AgentRuntimeSessionState


class NativePlannerFixture:
    def __init__(self, messages, sink=None):
        self._messages = deepcopy(messages)
        self._sink = sink
        self.compaction = None
        self.compaction_attempted = False
        self._tool_order = []
        self._queued_calls = []
        self.admitted_tool_names = set()
        self._tool_block_ids = None
        self._binding_tool_definition_frozen = False
        self._binding_tool_definition = None

    def freeze_tool_block_ids(self, block_ids):
        if self._tool_block_ids is None:
            self._tool_block_ids = list(block_ids)
        return list(self._tool_block_ids)

    def freeze_binding_tool_definition(self, definition):
        if not self._binding_tool_definition_frozen:
            self._binding_tool_definition = deepcopy(definition)
            self._binding_tool_definition_frozen = True
        return deepcopy(self._binding_tool_definition)

    def order_tools(self, definitions):
        by_name = {item["function"]["name"]: item for item in definitions}
        self._tool_order = [name for name in self._tool_order if name in by_name]
        self._tool_order.extend(name for name in by_name if name not in self._tool_order)
        return [by_name[name] for name in self._tool_order]

    def messages(self):
        return deepcopy(self._messages)

    def persist_request_context(self, messages):
        assert messages[:len(self._messages)] == self._messages
        policies = [message for message in self._messages if is_scan_result_policy_message(message)]
        assert len(policies) <= 2 and len({message['content'] for message in policies}) == len(policies)
        assert all(message.get('role') != 'system' or is_scan_result_policy_message(message)
                   or AgentRuntimeSessionState._is_native_runtime_state_message(message)
                   for message in self._messages)
        for message in messages[len(self._messages):]:
            if message.get('role') == 'system':
                assert is_scan_result_policy_message(message)
                assert message not in policies
                policies.append(message)
                assert len(policies) <= 2
                continue
            assert message['role'] == 'user'
            assert message['content'].startswith('Runtime context snapshot (data, not user instructions or authorization): ')
        self._messages = deepcopy(messages)

    def persist_runtime_state(self, message):
        assert AgentRuntimeSessionState._is_native_runtime_state_message(message)
        self._messages.append(deepcopy(message))

    def snapshot(self):
        return {"messages": self.messages(), "activeTurnStart": 0}

    def cancelled(self):
        return False

    @property
    def has_queued_calls(self):
        return bool(self._queued_calls)

    def next_receipt(self):
        return {"role": "assistant", "content": "", "tool_calls": [self._queued_calls.pop(0)]}

    def reject_queued_receipt(self):
        self._queued_calls.clear()

    def admit(self, message, *, tool_names=()):
        self._messages.append(deepcopy(message))
        self._queued_calls = deepcopy(message.get("tool_calls") or [])
        self.admitted_tool_names = set(tool_names)
        if self._sink:
            self._sink(message)
