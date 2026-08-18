import hashlib
import os
import sys
from pathlib import Path
from typing import Optional

import click
import pyperclip

from .app import NageApp
from .config import AppConfig
from .core import (
    ConfirmationService,
    RequestOptions,
    ResultRenderer,
    TerminalConfirmer,
    render_context_manifest,
)
from .errors import ExitCode, NageError
from .version import INSTALLATION_NOTICE, package_version


def create_app(confirmation: ConfirmationService) -> NageApp:
    return NageApp(confirmation=confirmation)


def _stdin_is_tty() -> bool:
    return bool(sys.stdin.isatty())


def _terminal_identity() -> Optional[str]:
    for name in ("TERM_SESSION_ID", "WT_SESSION", "TMUX_PANE", "SSH_TTY"):
        value = os.environ.get(name)
        if value:
            return "terminal-{0}".format(
                hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            )
    return None


def _confirmation_service() -> ConfirmationService:
    confirmer = TerminalConfirmer(
        input_fn=lambda prompt: click.prompt(
            prompt,
            default="",
            show_default=False,
            prompt_suffix="",
            err=True,
        ),
        output_fn=lambda message: click.echo(message, err=True),
    )
    return ConfirmationService(confirmer)


def _show_installation_notice(app: NageApp) -> None:
    try:
        should_show = app.config_store.consume_release_notice(package_version())
    except NageError:
        # The notice is informational. A marker read/write failure must never
        # prevent a request from running or change its exit status.
        return
    if should_show:
        click.echo("[notice] {0}".format(INSTALLATION_NOTICE), err=True)


def _configured(app: NageApp, config: AppConfig) -> AppConfig:
    environment_key = os.environ.get("NAGE_API_KEY", "")
    if config.api_key or environment_key:
        if environment_key and not config.api_key:
            return AppConfig(
                model=config.model,
                endpoint=config.endpoint,
                api_key=environment_key,
                memory_enabled=config.memory_enabled,
            )
        return config

    if not _stdin_is_tty():
        raise click.UsageError(
            "First-time setup requires a TTY or the NAGE_API_KEY environment variable."
        )
    click.echo("[nage] First-time provider setup.", err=True)
    model = click.prompt(
        "Model name",
        default=config.model,
        show_default=True,
        err=True,
    ).strip()
    endpoint = click.prompt(
        "API endpoint",
        default=config.endpoint,
        show_default=True,
        err=True,
    ).strip()
    api_key = click.prompt(
        "API key",
        hide_input=True,
        show_default=False,
        err=True,
    ).strip()
    if not api_key:
        raise click.UsageError("API key cannot be empty.")
    configured = AppConfig(
        model=model,
        endpoint=endpoint,
        api_key=api_key,
        memory_enabled=config.memory_enabled,
    )
    app.config_store.save(configured)
    click.echo("[nage] Provider configuration saved.", err=True)
    return configured


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=package_version(), prog_name="nage")
@click.option(
    "--dry-run", is_flag=True, help="Preview mutations without executing them."
)
@click.option("--fresh", is_flag=True, help="Do not attach a continuation capsule.")
@click.option("--no-memory", is_flag=True, help="Disable memory for this invocation.")
@click.option(
    "--show-context",
    is_flag=True,
    help="Show the content-selection manifest without segment bodies.",
)
@click.option(
    "--copy", "copy_output", is_flag=True, help="Copy final output explicitly."
)
@click.argument("query", nargs=-1)
def cli(
    query,
    dry_run: bool,
    fresh: bool,
    no_memory: bool,
    show_context: bool,
    copy_output: bool,
) -> None:
    """Run one natural-language terminal request and exit."""
    request_text = " ".join(query).strip()
    if not request_text:
        click.echo(click.get_current_context().get_help())
        return

    app = create_app(_confirmation_service())
    try:
        config = _configured(app, app.prepare())
        _show_installation_notice(app)
        stdin_stream = None
        if not _stdin_is_tty():
            stdin_stream = getattr(sys.stdin, "buffer", None)
        result = app.execute(
            request_text=request_text,
            config=config,
            working_directory=Path.cwd(),
            options=RequestOptions(
                dry_run=dry_run,
                fresh=fresh,
                no_memory=no_memory,
                show_context=show_context,
            ),
            stdin_stream=stdin_stream,
            terminal_identity=_terminal_identity(),
        )
        if show_context and result.context_manifest is not None:
            click.echo(
                render_context_manifest(result.context_manifest),
                err=True,
            )
        rendered = ResultRenderer().render(result.orchestration)
        if rendered.stderr:
            click.echo(rendered.stderr, err=True)
        if rendered.stdout:
            click.echo(rendered.stdout)
        if copy_output and rendered.stdout:
            try:
                pyperclip.copy(rendered.stdout)
                click.echo("[nage] Copied final output.", err=True)
            except Exception as exc:
                click.echo(
                    "[nage] Copy failed: {0}.".format(type(exc).__name__),
                    err=True,
                )
        if rendered.exit_code != ExitCode.SUCCESS:
            raise click.exceptions.Exit(int(rendered.exit_code))
    except NageError as exc:
        click.echo("[nage] Error: {0}".format(str(exc)), err=True)
        raise click.exceptions.Exit(int(exc.exit_code))
    except click.Abort:
        click.echo("[nage] Cancelled.", err=True)
        raise click.exceptions.Exit(int(ExitCode.CANCELLED))
