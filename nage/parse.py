"""Removed Nage 1.x response parser compatibility module."""


class JsonParser:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "JsonParser was removed in Nage 2. Use protocol.parse_assistant_decision."
        )
