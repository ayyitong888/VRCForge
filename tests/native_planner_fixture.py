"""Minimal receipt owner for planner-port tests; gateway tests use the real owner."""
from copy import deepcopy


class NativePlannerFixture:
    def __init__(self, messages, sink=None):
        self._messages = deepcopy(messages)
        self._sink = sink
        self.compaction = None
        self.compaction_attempted = False

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
