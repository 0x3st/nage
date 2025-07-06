"""
Main entry point for Nage - AI assisted terminal tool
"""

import click
import subprocess
from typing import List, Dict, Any
from rich.console import Console
from rich.panel import Panel

from .config import Config
from .lang import lang
from . import __version__

console = Console()
config = Config()


def get_message(key: str) -> str:
    """Get message in current language"""
    language = config.get_language()
    lang.set_language(language)
    return lang.get(key)


@click.group(invoke_without_command=True)
@click.option('--set-api', help='Set API endpoint')
@click.option('--set-key', help='Set API key')
@click.option('--set-model', help='Set AI model')
@click.option('--set-lang', help='Set language (zh/en)')
@click.option('--set', 'show_config', is_flag=True, help='Show current configuration')
@click.option('--version', is_flag=True, help='Show version information')
@click.argument('prompt', required=False)
@click.pass_context
def cli(ctx, set_api, set_key, set_model, set_lang, show_config, version, prompt):  # type: ignore
    # Show version information
    if version:
        console.print(Panel.fit(
            f"[bold blue]Nage[/bold blue] - AI assisted terminal tool\n\n"
            f"[bold]Version:[/bold] {__version__}\n"
            f"[bold]Description:[/bold] AI-powered terminal command suggestions\n\n"
            f"[dim]Project URL:[/dim] https://github.com/0x3st/nage\n"
            f"[dim]License:[/dim] MIT License",
            title="Version Information",
            border_style="blue"
        ))
        return
    
    # Handle configuration options
    if set_api:
        config.set_api_endpoint(set_api) # type: ignore
        console.print(f"{get_message('api_endpoint_set')}: {set_api}", style="green")
        return
    
    if set_key:
        config.set_api_key(set_key) # type: ignore
        console.print(get_message('api_key_set'), style="green")
        return
    
    if set_model:
        config.set_model(set_model) # type: ignore
        console.print(f"{get_message('model_set')}: {set_model}", style="green")
        return
    
    if set_lang:
        try:
            config.set_language(set_lang) # type: ignore
            console.print(f"{get_message('language_set')}: {set_lang}", style="green")
        except ValueError as e:
            console.print(f"{get_message('error')}: {str(e)}", style="red")
        return
    
    # Show current configuration
    if show_config:
        api_endpoint = config.get_api_endpoint()
        api_key = config.get_api_key()
        current_model = config.get_model()
        current_language = config.get_language()
        
        # Get language display name
        supported_languages = config.get_supported_languages()
        lang_display = supported_languages.get(current_language, current_language)
        
        console.print(Panel.fit(
            f"[bold]{get_message('api_endpoint')}:[/bold] {api_endpoint or '[red]' + get_message('not_configured') + '[/red]'}\n"
            f"[bold]{get_message('api_key')}:[/bold] {'[green]' + get_message('configured') + '[/green]' if api_key else '[red]' + get_message('not_configured') + '[/red]'}\n"
            f"[bold]{get_message('model')}:[/bold] {current_model}\n"
            f"[bold]{get_message('language')}:[/bold] {lang_display} ({current_language})\n"
            f"[bold]{get_message('config_file')}:[/bold] {config.config_file}\n"
            f"[bold]{get_message('status')}:[/bold] {'[green]' + get_message('ready_to_use') + '[/green]' if config.is_configured() else '[red]' + get_message('setup_required') + '[/red]'}",
            title=f"{get_message('current_configuration')} v{__version__}",
            border_style="blue"
        ))
        return
    
    # If no subcommand and no prompt, show help
    if prompt is None:
        console.print(Panel.fit(
            f"[bold blue]Nage[/bold blue] - AI assisted terminal tool [dim]v{__version__}[/dim]\n\n"
            f"[bold]{get_message('help_usage')}:[/bold]\n"
            "  nage --set-api=\"<endpoint>\"   Set API endpoint\n"
            "  nage --set-key=\"<key>\"        Set API key\n"
            "  nage --set-model=\"<model>\"    Set AI model\n"
            "  nage --set-lang=\"<lang>\"      Set language (zh/en)\n"
            "  nage --set                    Show current configuration\n"
            "  nage --version                Show version information\n"
            "  nage \"<prompt>\"               Ask AI for help\n\n"
            f"[bold]{get_message('help_examples')}:[/bold]\n"
            "  nage \"how to find large files\"\n"
            "  nage \"git commit best practices\"\n"
            "  nage \"compress folder with tar\"\n"
            "  nage --set-lang=en\n"
            "  nage --set-model=gpt-4",
            title=get_message('help_title'),
            border_style="blue"
        ))
        return
    
    # Check if configured
    if not config.is_configured():
        console.print(Panel.fit(
            f"[bold red]{get_message('configuration_required')}[/bold red]\n\n"
            f"{get_message('setup_required_msg')}:\n\n"
            f"[bold]1.[/bold] {get_message('setup_step1')}:\n"
            "   nage --set-api=\"https://api.openai.com/v1/chat/completions\"\n\n"
            f"[bold]2.[/bold] {get_message('setup_step2')}:\n"
            "   nage --set-key=\"your-api-key-here\"\n\n"
            f"[dim]{get_message('config_stored_msg')}[/dim]",
            title=get_message('setup_required'),
            border_style="red"
        ))
        return  # Add return statement instead of sys.exit(1)
    
    # Process the prompt
    try:
        with console.status(f"[bold green]{get_message('asking_ai')}[/bold green]", spinner="dots"):
            from .ai_client import AIClient
            ai_client = AIClient(config)
            response = ai_client.send_request(prompt) # type: ignore
        
        # Handle error in response
        if "error" in response:
            console.print(Panel.fit(
                f"[bold red]{get_message('error')}:[/bold red] {response['error']}\n\n"
                f"[dim]{get_message('check_config')}[/dim]",
                title=get_message('error'),
                border_style="red"
            ))
            return
        
        # Handle request for more input
        if response.get("requires_input", False):
            handle_input_request(response)
            return
        
        # Display commands and ask for confirmation
        handle_command_execution(response, prompt) # type: ignore
        
    except Exception as e:
        console.print(Panel.fit(
            f"[bold red]{get_message('error')}:[/bold red] {str(e)}\n\n"
            f"[dim]{get_message('check_config')}[/dim]",
            title=get_message('error'),
            border_style="red"
        ))
        return


def handle_input_request(response: Dict[str, Any]) -> None:
    """Handle when AI needs more input from user"""
    console.print(Panel.fit(
        f"[bold blue]{get_message('needs_more_info')}[/bold blue]\n\n"
        f"[bold]{get_message('description')}:[/bold] {response.get('description', '')}\n\n"
        f"[bold]{get_message('answer_questions')}:[/bold]\n" +
        '\n'.join(f"  {i+1}. {q}" for i, q in enumerate(response.get('input_prompts', []))),
        title=get_message('needs_more_info'),
        border_style="blue"
    ))


def handle_command_execution(response: Dict[str, Any], original_prompt: str) -> None:
    """Handle command execution with user confirmation"""
    commands = response.get("commands", [])
    description = response.get("description", "")
    warnings = response.get("warnings", [])
    
    if not commands:
        # If no commands, just show description
        console.print(Panel.fit(
            f"[bold blue]{get_message('ai_reply')}:[/bold blue]\n\n{description}",
            title=f"{get_message('reply')}: {original_prompt[:50]}{'...' if len(original_prompt) > 50 else ''}",
            border_style="blue"
        ))
        return
    
    # Show commands and description
    command_text = ""
    for i, cmd in enumerate(commands, 1):
        command_text += f"{i}. [bold cyan]{cmd}[/bold cyan]\n"
    
    info_text = f"[bold green]{get_message('recommended_commands')}:[/bold green]\n\n{command_text}\n"
    if description:
        info_text += f"[bold]{get_message('explanation')}:[/bold] {description}\n\n"
    
    if warnings:
        info_text += f"[bold yellow]{get_message('warnings')}:[/bold yellow]\n"
        for warning in warnings:
            info_text += f"  * {warning}\n"
        info_text += "\n"
    
    console.print(Panel.fit(
        info_text + f"[dim]{get_message('execute_commands')}[/dim]",
        title=f"{get_message('command_suggestions')}: {original_prompt[:50]}{'...' if len(original_prompt) > 50 else ''}",
        border_style="green"
    ))
    
    # Get user confirmation
    try:
        choice = input().strip().lower()
        if choice in ['y', 'yes']:
            execute_commands(commands)
        elif choice in ['s', 'selective']:
            execute_commands_selective(commands)
        else:
            console.print(f"[dim]{get_message('commands_cancelled')}[/dim]")
    except KeyboardInterrupt:
        console.print(f"\n[dim]{get_message('commands_cancelled')}[/dim]")


def execute_commands(commands: List[str]):
    """Execute all commands"""
    for i, cmd in enumerate(commands, 1):
        console.print(f"\n[bold]{get_message('executing_command')} {i}/{len(commands)}:[/bold] [cyan]{cmd}[/cyan]")
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                if result.stdout:
                    console.print(result.stdout)
                console.print(f"[green]{get_message('command_success')}[/green]")
            else:
                console.print(f"[red]{get_message('command_failed')} (return code: {result.returncode})[/red]")
                if result.stderr:
                    console.print(f"[red]{get_message('error_info')}: {result.stderr}[/red]")
        except Exception as e:
            console.print(f"[red]{get_message('execution_error')}: {e}[/red]")


def execute_commands_selective(commands: List[str]):
    """Execute commands selectively"""
    for i, cmd in enumerate(commands, 1):
        console.print(f"\n[bold]{get_message('executing_command')} {i}/{len(commands)}:[/bold] [cyan]{cmd}[/cyan]")
        try:
            choice = input(f"{get_message('execute_command')}: ").strip().lower()
            if choice in ['q', 'quit']:
                console.print(f"[dim]{get_message('stopped_execution')}[/dim]")
                break
            elif choice in ['y', 'yes']:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode == 0:
                    if result.stdout:
                        console.print(result.stdout)
                    console.print(f"[green]{get_message('command_success')}[/green]")
                else:
                    console.print(f"[red]{get_message('command_failed')} (return code: {result.returncode})[/red]")
                    if result.stderr:
                        console.print(f"[red]{get_message('error_info')}: {result.stderr}[/red]")
            else:
                console.print(f"[dim]{get_message('skipped_command')}[/dim]")
        except KeyboardInterrupt:
            console.print(f"\n[dim]{get_message('stopped_execution')}[/dim]")
            break
        except Exception as e:
            console.print(f"[red]{get_message('execution_error')}: {e}[/red]")
