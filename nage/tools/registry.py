from types import MappingProxyType
from typing import Dict, Iterable, Mapping, Tuple

from ..errors import ToolError
from .base import ToolSpec


class ToolRegistry:
    """Mutable only during application composition."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError("Tool is already registered: {0}".format(spec.name))
        self._tools[spec.name] = spec

    def snapshot(self) -> "ToolRegistrySnapshot":
        return ToolRegistrySnapshot(dict(self._tools))


class ToolRegistrySnapshot:
    """Immutable tool set used for exactly one invocation."""

    def __init__(self, tools: Mapping[str, ToolSpec]) -> None:
        self._tools = MappingProxyType(dict(tools))

    def resolve(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(
                "Tool is not available in this invocation: {0}".format(name)
            ) from exc

    def specs(self) -> Tuple[ToolSpec, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    def names(self) -> Tuple[str, ...]:
        return tuple(sorted(self._tools))

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterable[ToolSpec]:
        return iter(self.specs())
