import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

import nage.cli as cli_module
from nage.app import NageApp
from nage.config import ConfigStore
from nage.core import ConfirmationService
from nage.paths import AppPaths
from nage.protocol import (
    AssistantDecision,
    ContinuationProposal,
    DecisionStatus,
    ToolCall,
)
from nage.providers import (
    FakeProvider,
    MessageRole,
    ProviderMessage,
    ProviderResult,
)


def provider_result(message: str = "Hello from Nage") -> ProviderResult:
    decision = AssistantDecision(status=DecisionStatus.FINAL, message=message)
    return ProviderResult(
        decision=decision,
        assistant_message=ProviderMessage(
            MessageRole.ASSISTANT,
            decision.model_dump_json(),
        ),
    )


def inject_app(tmp_path: Path, monkeypatch, responses=None):
    provider = FakeProvider(responses or [provider_result()])

    def create(confirmation: ConfirmationService):
        return NageApp(
            paths=AppPaths(tmp_path / "data"),
            provider=provider,
            confirmation=confirmation,
        )

    monkeypatch.setattr(cli_module, "create_app", create)
    return provider, AppPaths(tmp_path / "data")


def test_no_request_prints_usage_without_first_run_setup(
    isolated_home: Path,
) -> None:
    result = CliRunner().invoke(cli_module.cli, [])

    assert result.exit_code == 0
    assert "Run one natural-language terminal request" in result.output
    assert "First-time provider setup" not in result.output
    assert not (isolated_home / ".nage").exists()


def test_first_run_setup_hides_key_and_executes_one_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, paths = inject_app(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_module, "_stdin_is_tty", lambda: True)

    result = CliRunner().invoke(
        cli_module.cli,
        ["hello", "nage"],
        input="\n\ntest-secret-key\n",
    )

    assert result.exit_code == 0
    assert "Hello from Nage" in result.output
    assert "test-secret-key" not in result.output
    assert ConfigStore(paths).load().api_key == "test-secret-key"
    assert not paths.legacy_history_file.exists()


def test_first_run_with_piped_stdin_requires_env_or_tty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    inject_app(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_module, "_stdin_is_tty", lambda: False)

    result = CliRunner().invoke(
        cli_module.cli,
        ["summarize", "stdin"],
        input="untrusted build log\n",
    )

    assert result.exit_code == 2
    assert "requires a TTY" in result.output
    assert "untrusted build log" not in result.output


def test_installation_notice_is_shown_once_per_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, paths = inject_app(
        tmp_path,
        monkeypatch,
        responses=[provider_result(), provider_result()],
    )
    paths.ensure()
    ConfigStore(paths).save(cli_module.AppConfig(api_key="test-key"))

    runner = CliRunner()
    first = runner.invoke(cli_module.cli, ["answer", "me"])
    second = runner.invoke(cli_module.cli, ["answer", "me"])

    notice = (
        "[notice] Nage is a free software under GPLv3 by 0x3st. "
        "Go Linux Do, learn AI."
    )
    assert first.exit_code == 0
    assert first.output.count(notice) == 1
    assert second.exit_code == 0
    assert notice not in second.output


def test_help_and_version_do_not_show_installation_notice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, paths = inject_app(tmp_path, monkeypatch)
    paths.ensure()
    ConfigStore(paths).save(cli_module.AppConfig(api_key="test-key"))

    runner = CliRunner()
    help_result = runner.invoke(cli_module.cli, ["--help"])
    version_result = runner.invoke(cli_module.cli, ["--version"])

    assert "[notice]" not in help_result.output
    assert "[notice]" not in version_result.output
    assert not paths.release_notice_file.exists()


def test_cli_does_not_copy_without_explicit_option(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, paths = inject_app(tmp_path, monkeypatch)
    paths.ensure()
    ConfigStore(paths).save(cli_module.AppConfig(api_key="test-key"))
    calls = []
    monkeypatch.setattr(
        cli_module.pyperclip,
        "copy",
        lambda value: calls.append(value),
    )

    result = CliRunner().invoke(cli_module.cli, ["answer", "me"])

    assert result.exit_code == 0
    assert calls == []


def test_cli_copy_and_show_context_are_explicit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, paths = inject_app(tmp_path, monkeypatch)
    paths.ensure()
    ConfigStore(paths).save(cli_module.AppConfig(api_key="test-key"))
    calls = []
    monkeypatch.setattr(
        cli_module.pyperclip,
        "copy",
        lambda value: calls.append(value),
    )

    result = CliRunner().invoke(
        cli_module.cli,
        ["--copy", "--show-context", "answer", "me"],
    )

    assert result.exit_code == 0
    assert calls == ["Hello from Nage"]
    assert "estimated prompt tokens" in result.output
    assert "Copied final output" in result.output


def test_cli_confirmation_executes_exact_file_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tool_decision = AssistantDecision(
        status=DecisionStatus.TOOL_CALL,
        tool_calls=[
            ToolCall(
                id="call_1",
                name="files.create_text",
                arguments={"path": "created.txt", "content": "created"},
            )
        ],
    )
    responses = [
        ProviderResult(
            decision=tool_decision,
            assistant_message=ProviderMessage(
                MessageRole.ASSISTANT,
                tool_decision.model_dump_json(),
                tool_calls=tuple(tool_decision.tool_calls),
            ),
        ),
        provider_result("Created created.txt."),
    ]
    _, paths = inject_app(tmp_path, monkeypatch, responses=responses)
    paths.ensure()
    ConfigStore(paths).save(cli_module.AppConfig(api_key="test-key"))
    monkeypatch.setattr(cli_module, "_stdin_is_tty", lambda: True)
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(
            cli_module.cli,
            ["create", "created.txt"],
            input="y\n",
        )
        created = Path("created.txt")
        assert created.read_text(encoding="utf-8") == "created"

    assert result.exit_code == 0
    assert "Planned action" in result.output
    assert "Continue?" in result.output
    assert "Created created.txt." in result.output


def test_cli_needs_input_uses_stable_exit_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    decision = AssistantDecision(
        status=DecisionStatus.NEEDS_INPUT,
        message="Who should receive it?",
        continuation=ContinuationProposal(
            label="Send email",
            intent="mail.send",
            known={"subject": "Update"},
            missing=["recipient"],
        ),
    )
    response = ProviderResult(
        decision=decision,
        assistant_message=ProviderMessage(
            MessageRole.ASSISTANT,
            decision.model_dump_json(),
        ),
    )
    _, paths = inject_app(tmp_path, monkeypatch, responses=[response])
    paths.ensure()
    ConfigStore(paths).save(cli_module.AppConfig(api_key="test-key"))

    result = CliRunner().invoke(cli_module.cli, ["send", "an", "email"])

    assert result.exit_code == 3
    assert "Who should receive it?" in result.output
    assert "Saved continuation" in result.output


def test_python_module_entry_point_is_preserved() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "nage", "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "nage, version 2.0.0a1" in result.stdout


def test_direct_script_entry_point_is_preserved() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    entry_point = repository_root / "nage" / "__main__.py"
    result = subprocess.run(
        [sys.executable, str(entry_point), "--version"],
        cwd=str(repository_root),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "nage, version 2.0.0a1" in result.stdout


def test_cli_version_uses_package_metadata() -> None:
    result = CliRunner().invoke(cli_module.cli, ["--version"])

    assert result.exit_code == 0
    assert "nage, version 2.0.0a1" in result.output
