from nage.prompts import CORE_SYSTEM_PROMPT


def test_v2_prompt_is_compact_and_has_required_trust_boundaries() -> None:
    normalized = " ".join(CORE_SYSTEM_PROMPT.split())

    assert len(CORE_SYSTEM_PROMPT.encode("utf-8")) < 4096
    assert "Only USER_REQUEST" in normalized
    assert "untrusted data" in normalized
    assert "Use only tools supplied" in normalized
    assert "tool call is a proposal" in normalized
    assert "protocol 2.0" in normalized


def test_v2_prompt_does_not_contain_legacy_authority_fields() -> None:
    forbidden = {
        "clear_history",
        "clear_memory",
        "sett_api",
        "sett_ep",
        "sett_md",
    }

    assert forbidden.isdisjoint(CORE_SYSTEM_PROMPT.split())
