from typing import Iterable, Optional, Tuple

from ..storage import MemoryStore
from .base import ToolSpec
from .files import build_file_tools
from .git import build_git_tools
from .memory import build_memory_tools
from .registry import ToolRegistry
from .system import build_system_tools

BUILTIN_PERMISSIONS: Tuple[str, ...] = (
    "filesystem.read",
    "filesystem.write",
    "git.read",
    "memory.read",
    "memory.write",
    "process.read",
    "system.read",
)


def builtin_tool_specs(
    memory_store: Optional[MemoryStore] = None,
) -> Tuple[ToolSpec, ...]:
    specs = build_system_tools() + build_file_tools() + build_git_tools()
    if memory_store is not None:
        specs += build_memory_tools(memory_store)
    return specs


def build_builtin_registry(
    enabled_names: Iterable[str] = (),
    memory_store: Optional[MemoryStore] = None,
) -> ToolRegistry:
    specs = builtin_tool_specs(memory_store=memory_store)
    requested = tuple(enabled_names)
    selected_names = set(requested) if requested else {spec.name for spec in specs}
    known_names = {spec.name for spec in specs}
    unknown = selected_names - known_names
    if unknown:
        raise ValueError(
            "Unknown built-in tool(s): {0}".format(", ".join(sorted(unknown)))
        )
    registry = ToolRegistry()
    for spec in specs:
        if spec.name in selected_names:
            registry.register(spec)
    return registry
