"""
Main entry point for Ya        "warn        "error_info"        "warnings": "Warning",
        "execute_commands": "Execute these commands? (y/n/s for selective)",
        "command_suggestions": "Command Suggestions",
        "commands_cancelled": "Commands cancelled",
        "executing_command": "Executing command",
        "command_success": "Command executed successfully",
        "command_failed": "Command execution failed",r info",
        "execution_error": "Error executing command",
        "execute_command": "Execute this command? (y/n/q for quit)",
        "stopped_execution": "Stopped executing remaining commands",
        "skipped_command": "Skipped this command",
        "asking_ai": "Asking AI..." "Warning",
        "execute_commands": "Execute these commands? (y/n/s for selective)",
        "command_suggestions": "Command Suggestions",
        "commands_cancelled": "Commands cancelled",
        "executing_command": "Executing command",
        "command_success": "Command executed successfully",
        "command_failed": "Command execution failed",ool
"""

import click
import sys
import subprocess
from typing import List, Dict, Any
from rich.console import Console
from rich.panel import Panel

from .config import Config
from .ai_client import AIClient
from . import __version__

console = Console()
config = Config()

# 多语言支持
MESSAGES = {
    "zh": {
        "needs_more_info": "需要更多信息",
        "description": "描述",
        "answer_questions": "请回答以下问题",
        "ai_reply": "AI 回复",
        "reply": "回复",
        "recommended_commands": "推荐执行的命令",
        "explanation": "说明",
        "warnings": "警告",
        "execute_commands": "是否执行这些命令? (y/n/s for selective)",
        "command_suggestions": "命令建议",
        "commands_cancelled": "命令已取消执行",
        "executing_command": "执行命令",
        "command_success": "命令执行成功",
        "command_failed": "命令执行失败",
        "error_info": "错误信息",
        "execution_error": "执行命令时出错",
        "execute_command": "执行此命令? (y/n/q for quit)",
        "stopped_execution": "已停止执行剩余命令",
        "skipped_command": "跳过此命令",
        "asking_ai": "正在询问AI..."
    },
    "en": {
        "needs_more_info": "More Information Needed",
        "description": "Description",
        "answer_questions": "Please answer the following questions",
        "ai_reply": "AI Reply",
        "reply": "Reply",
        "recommended_commands": "Recommended commands to execute",
        "explanation": "Explanation",
        "warnings": "Warnings",
        "execute_commands": "Execute these commands? (y/n/s for selective)",
        "command_suggestions": "Command Suggestions",
        "commands_cancelled": "Commands cancelled",
        "executing_command": "Executing command",
        "command_success": "Command executed successfully",
        "command_failed": "Command execution failed",
        "error_info": "Error info",
        "execution_error": "Error executing command",
        "execute_command": "Execute this command? (y/n/q for quit)",
        "stopped_execution": "Stopped executing remaining commands",
        "skipped_command": "Skipped this command",
        "asking_ai": "Asking AI..."
    }
}

def get_message(key: str) -> str:
    """Get message in current language"""
    lang = config.get_language()
    return MESSAGES.get(lang, MESSAGES["zh"]).get(key, key)


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
    """Yao - AI assisted terminal tool
    
    Ask AI for help with terminal commands and tools.
    """
    
    # Show version information
    if version:
        console.print(Panel.fit(
            f"[bold blue]Yao[/bold blue] - AI assisted terminal tool\n\n"
            f"[bold]Version:[/bold] {__version__}\n"
            f"[bold]Description:[/bold] AI-powered terminal command suggestions\n\n"
            f"[dim]Project URL:[/dim] https://github.com/0x3st/yao\n"
            f"[dim]License:[/dim] MIT License",
            title="Version Information",
            border_style="blue"
        ))
        return
    
    # Handle configuration options
    if set_api:
        config.set_api_endpoint(set_api) # type: ignore
        console.print(f"API endpoint set to: {set_api}", style="green")
        return
    
    if set_key:
        config.set_api_key(set_key) # type: ignore
        console.print("API key has been set", style="green")
        return
    
    if set_model:
        config.set_model(set_model) # type: ignore
        console.print(f"Model set to: {set_model}", style="green")
        return
    
    if set_lang:
        try:
            config.set_language(set_lang) # type: ignore
            console.print(f"Language set to: {set_lang}", style="green")
        except ValueError as e:
            console.print(f"Error: {str(e)}", style="red")
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
        
        # 显示预设端点
        preset_info = "\n[bold]Available Preset Endpoints:[/bold]\n"
        for name, url in config.get_preset_endpoints().items():
            preset_info += f"  {name}: {url}\n"
        
        # 显示支持的语言
        language_info = "\n[bold]Supported Languages:[/bold]\n"
        for code, name in supported_languages.items():
            marker = " (current)" if code == current_language else ""
            language_info += f"  {code}: {name}{marker}\n"
        
        console.print(Panel.fit(
            f"[bold blue]Current Configuration[/bold blue] [dim]v{__version__}[/dim]\n\n"
            f"[bold]API Endpoint:[/bold] {api_endpoint or '[red]Not configured[/red]'}\n"
            f"[bold]API Key:[/bold] {'[green]Configured[/green]' if api_key else '[red]Not configured[/red]'}\n"
            f"[bold]Model:[/bold] {current_model}\n"
            f"[bold]Language:[/bold] {lang_display} ({current_language})\n"
            f"[bold]Config File:[/bold] {config.config_file}\n"
            f"{preset_info}\n"
            f"{language_info}\n"
            f"[dim]Status:[/dim] {'[green]Ready to use[/green]' if config.is_configured() else '[red]Setup required[/red]'}\n\n"
            f"[dim]Usage examples:[/dim]\n"
            f"[dim]  yao --set-api=deepseek[/dim]\n"
            f"[dim]  yao --set-lang=zh[/dim]\n"
            f"[dim]  yao --set-model=gpt-4[/dim]",
            title="Configuration",
            border_style="blue"
        ))
        return
    
    # If no subcommand and no prompt, show help
    if prompt is None:
        console.print(Panel.fit(
            f"[bold blue]Yao[/bold blue] - AI assisted terminal tool [dim]v{__version__}[/dim]\n\n"
            "[bold]Usage:[/bold]\n"
            "  yao --set-api=\"<endpoint>\"   Set API endpoint\n"
            "  yao --set-key=\"<key>\"        Set API key\n"
            "  yao --set-model=\"<model>\"    Set AI model\n"
            "  yao --set-lang=\"<lang>\"      Set language (zh/en)\n"
            "  yao --set                    Show current configuration\n"
            "  yao --version                Show version information\n"
            "  yao \"<prompt>\"               Ask AI for help\n\n"
            "[bold]Examples:[/bold]\n"
            "  yao \"how to find large files\"\n"
            "  yao \"git commit best practices\"\n"
            "  yao \"compress folder with tar\"\n"
            "  yao --set-lang=en\n"
            "  yao --set-model=gpt-4",
            title="Help",
            border_style="blue"
        ))
        return
    
    # Check if configured
    if not config.is_configured():
        console.print(Panel.fit(
            "[bold red]Configuration Required[/bold red]\n\n"
            "Please configure your API endpoint and key first:\n\n"
            "[bold]1.[/bold] Set API endpoint:\n"
            "   yao --set-api=\"https://api.openai.com/v1/chat/completions\"\n\n"
            "[bold]2.[/bold] Set API key:\n"
            "   yao --set-key=\"your-api-key-here\"\n\n"
            "[dim]Your configuration is stored in ~/.yao/config.json[/dim]",
            title="Setup Required",
            border_style="red"
        ))
        sys.exit(1)
    
    # Process the prompt
    try:
        with console.status(f"[bold green]{get_message('asking_ai')}[/bold green]", spinner="dots"):
            ai_client = AIClient(config)
            response = ai_client.send_request(prompt) # type: ignore
        
        # Handle error in response
        if "error" in response:
            console.print(Panel.fit(
                f"[bold red]Error:[/bold red] {response['error']}\n\n"
                "[dim]Please check your API configuration and try again.[/dim]",
                title="Error",
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
            f"[bold red]Error:[/bold red] {str(e)}\n\n"
            "[dim]Please check your API configuration and try again.[/dim]",
            title="Error",
            border_style="red"
        ))
        sys.exit(1)


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
        if choice in ['y', 'yes', '是', 'y是']:
            execute_commands(commands)
        elif choice in ['s', 'selective', '选择']:
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
                console.print(f"[red]{get_message('command_failed')} (返回码: {result.returncode})[/red]")
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
            if choice in ['q', 'quit', '退出']:
                console.print(f"[dim]{get_message('stopped_execution')}[/dim]")
                break
            elif choice in ['y', 'yes', '是']:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode == 0:
                    if result.stdout:
                        console.print(result.stdout)
                    console.print(f"[green]{get_message('command_success')}[/green]")
                else:
                    console.print(f"[red]{get_message('command_failed')} (返回码: {result.returncode})[/red]")
                    if result.stderr:
                        console.print(f"[red]{get_message('error_info')}: {result.stderr}[/red]")
            else:
                console.print(f"[dim]{get_message('skipped_command')}[/dim]")
        except KeyboardInterrupt:
            console.print(f"\n[dim]{get_message('stopped_execution')}[/dim]")
            break
        except Exception as e:
            console.print(f"[red]{get_message('execution_error')}: {e}[/red]")
