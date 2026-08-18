import pytest

from nage.ai_client import AIClient
from nage.setting import Setting


def test_legacy_setting_is_an_inactive_compatibility_shim() -> None:
    with pytest.raises(RuntimeError, match="removed in Nage 2"):
        Setting()


def test_legacy_ai_client_is_an_inactive_compatibility_shim() -> None:
    with pytest.raises(RuntimeError, match="removed in Nage 2"):
        AIClient()
