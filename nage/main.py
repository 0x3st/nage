import click
import pyperclip
from .ai_client import AICLient
from .setting import setting
from .parse import ParseJSON
from . import __version__


def copy_to_clipboard(text):
    """将文本复制到剪贴板"""
    try:
        pyperclip.copy(text)
        return True
    except Exception as e:
        print(f"[nage] Warning: Failed to copy to clipboard: {e}")
        return False


def setup():
    sett = setting()
    loaded = sett.load()
    if not loaded or not sett.key:
        print("[nage] First time setup. Please enter the following information (press Enter to use default):")
        model = input("Model name (default: deepseek-chat): ").strip() or "deepseek-chat"
        endpoint = input("API endpoint (default: https://api.deepseek.com/v1): ").strip() or "https://api.deepseek.com/v1"
        api_key = input("API key (required): ").strip()
        if not api_key:
            print("[nage] API key cannot be empty. Exiting.")
            return None
        sett.change_model(model)
        sett.change_endpoint(endpoint)
        sett.change_key(api_key)
        sett.save()
        print("[nage] Setup complete. You can now use the tool.")
        return sett
    return sett


@click.command()
@click.argument('query', nargs=-1)
def cli(query):
    """Nage: Conversational AI assistant. Just type your request."""
    sett = setup()
    if sett is None:
        return
    
    # 如果没有输入参数，只显示信息然后退出
    if not query:
        docs_url = "https://github.com/0x3st/nage"
        print("This is a free tool by 0x3st. You can start by just ask.")
        print(f"Go to {docs_url} for further information.")
        print(f"nage-{__version__}-{sett.model}")
        return
    
    question = " ".join(query)
    if not question.strip():
        print("[nage] Please enter a question or command.")
        return
    ai = AICLient()
    response = ai.request(question)
    parsed = ParseJSON(response)
    t = parsed.read_type()
    if t == "sett_api":
        sett.change_key(parsed.read_content())
        sett.save()
        print(parsed.read_msg())
    elif t == "sett_ep":
        sett.change_endpoint(parsed.read_content())
        sett.save()
        print(parsed.read_msg())
    elif t == "sett_md":
        sett.change_model(parsed.read_content())
        sett.save()
        print(parsed.read_msg())
    elif t == "memo":
        sett.add_memo(parsed.read_content())
        print(parsed.read_msg())
    elif t == "ask":
        content = parsed.read_content()
        message = parsed.read_msg()
        print(message)
        
        # 如果有内容就直接复制到剪贴板
        if content and content.strip():
            if copy_to_clipboard(content):
                print(f"[nage] 💾 Copied to clipboard")
                pass
            else:
                print(f"[nage] Failed to copy command to clipboard")
    elif t == "error":
        print(f"[nage] Error: {parsed.read_msg()}")
    else:
        print(f"[nage] Unknown response: {response}")


if __name__ == "__main__":
    cli()
