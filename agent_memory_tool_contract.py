"""Memory tool identities shared by runtime handlers and lightweight catalogues."""

MEMORY_WRITE_TOOLS = frozenset({"vrcforge_remember_memory", "vrcforge_delete_memory"})
MEMORY_TOOL_NAMES = MEMORY_WRITE_TOOLS | {"vrcforge_list_memory"}
