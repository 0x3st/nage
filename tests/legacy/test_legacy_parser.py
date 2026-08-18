import pytest

from nage.parse import JsonParser


def test_legacy_parser_is_an_inactive_compatibility_shim() -> None:
    with pytest.raises(RuntimeError, match="removed in Nage 2"):
        JsonParser("{}")
