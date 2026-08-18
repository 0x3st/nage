"""Removed Nage 1.x provider client compatibility module."""


class AIClient:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "AIClient was removed in Nage 2. Use NageApp and a provider adapter."
        )
