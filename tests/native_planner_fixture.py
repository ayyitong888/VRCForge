"""Minimal receipt owner for planner-port tests; gateway tests use the real owner."""
from copy import deepcopy


class NativePlannerFixture:
    def __init__(self, messages, sink=None):
        self._messages = deepcopy(messages)
        self._sink = sink
        self.compaction = None
        self.compaction_attempted = False
        self._tool_order = []

    def order_tools(self, definitions):
        by_name = {item["function"]["name"]: item for item in definitions}
        self._tool_order = [name for name in self._tool_order if name in by_name]
        self._tool_order.extend(name for name in by_name if name not in self._tool_order)
        return [by_name[name] for name in self._tool_order]

    def messages(self):
        return deepcopy(self._messages)

    def snapshot(self):
        return {"messages": self.messages(), "activeTurnStart": 0}

    def cancelled(self):
        return False

    def admit(self, message):
        self._messages.append(deepcopy(message))
        if self._sink:
            self._sink(message)
