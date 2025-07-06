# Nage - AI Assisted Terminal Tool

<div align="center">

![Nage Logo](https://img.shields.io/badge/Nage-AI%20Terminal%20Assistant-blue?style=for-the-badge)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8+-blue?style=for-the-badge&logo=python)](https://www.python.org/downloads/)

</div>

Nage is a powerful Python-based AI assistant that helps you remember and discover terminal commands. Simply describe what you want to do, and Nage will suggest the right commands with explanations!

## 🎬 Demo

```bash
$ nage "find large files in current directory"
```

```
╭───── Command Suggestions: find large files in current directory ─────╮
│ Recommended commands to execute:                                      │
│                                                                       │
│ 1. find . -type f -size +100M -exec ls -lh {} \; | sort -k5 -hr     │
│                                                                       │
│ Explanation: Find files larger than 100MB and sort by size           │
│                                                                       │
│ Execute these commands? (y/n/s for selective)                         │
╰───────────────────────────────────────────────────────────────────────╯
```

## Features

- 🤖 AI-powered terminal command suggestions
- ⚙️ Easy configuration management with preset API endpoints
- 🎨 Beautiful terminal output with rich formatting
- 🔐 Secure API key storage
- 🌍 Multi-language support with extensible architecture (English/Chinese)
- 🔧 Customizable AI models and providers
- 📦 Simple installation with `uv` package manager
- 🚀 Interactive command execution with confirmation

## Installation

This project is managed by `uv`. To install and set up:

```bash
# Install dependencies
uv sync

# Install the package in development mode
uv pip install -e .

# Or install directly from the repository
pip install git+https://github.com/0x3st/nage.git
```

## Quick Start

1. **Install the package:**
   ```bash
   uv sync && uv pip install -e .
   ```

2. **Configure API (choose one):**
   ```bash
   # Using DeepSeek (recommended)
   nage --set-api=deepseek
   nage --set-key="your-deepseek-api-key"
   
   # Using OpenAI
   nage --set-api=openai
   nage --set-key="your-openai-api-key"
   ```

3. **Start using:**
   ```bash
   nage "how to find large files"
   ```

## Configuration

Before using Nage, you need to configure your AI API endpoint and key:

### 1. Set API Endpoint

You can use a full URL or a preset alias:

```bash
# Using DeepSeek API (recommended)
nage --set-api="https://api.deepseek.com/chat/completions"

# Using DeepSeek v1 API (OpenAI compatible)
nage --set-api="https://api.deepseek.com/v1/chat/completions"

# Using OpenAI API
nage --set-api="https://api.openai.com/v1/chat/completions"

# Using preset aliases
nage --set-api=deepseek       # Same as https://api.deepseek.com/chat/completions
nage --set-api=openai         # Same as https://api.openai.com/v1/chat/completions
```

### 2. Set API Key
```bash
nage --set-key="your-api-key-here"
```

### 3. Set Language (Optional)
```bash
# Set to English (default)
nage --set-lang=en

# Set to Chinese
nage --set-lang=zh

# Language switching affects all UI text and AI prompts
```

### 4. Set AI Model (Optional)
```bash
# Set custom model
nage --set-model="gpt-4"
nage --set-model="deepseek-chat"
nage --set-model="claude-3-haiku-20240307"
```

### 5. View Configuration
```bash
nage --set
```

Your configuration is stored securely in `~/.nage/config.json`.

## Usage

### Basic Commands

1. **Configure API endpoint:**
   ```bash
   nage --set-api="api endpoint"
   # Or use preset aliases
   nage --set-api=deepseek
   ```

2. **Configure API key:**
   ```bash
   nage --set-key="your-api-key"
   ```

3. **Set language:**
   ```bash
   nage --set-lang=en    # English (default)
   nage --set-lang=zh    # Chinese
   ```

4. **Set AI model:**
   ```bash
   nage --set-model="model-name"
   ```

5. **Ask AI for help:**
   ```bash
   nage "your question or prompt"
   ```

6. **Interactive command execution:**
   - Type `y` to execute all suggested commands
   - Type `s` for selective execution (choose which commands to run)
   - Type `n` to cancel execution

### Examples

```bash
# Get help with finding large files
nage "how to find large files in current directory"

# Learn about git best practices
nage "git commit best practices"

# Compress files with tar
nage "how to compress a folder with tar"

# System monitoring
nage "show CPU and memory usage"

# Configuration examples
nage --set-lang=zh
nage --set-model=gpt-4
nage --set-api=openai
```

### Additional Commands

- **Show help:**
  ```bash
  nage
  ```

- **Show version:**
  ```bash
  nage --version
  ```

## Language Support

Nage features a flexible language management system:

### Supported Languages
- **English (en)** - Default
- **Chinese (zh)** - 中文支持

### Language Features
- **Dynamic switching:** Change language anytime with `--set-lang`
- **Complete localization:** All UI text, error messages, and prompts
- **Extensible architecture:** Easy to add new languages

### Adding New Languages
Developers can easily add new languages by extending the language manager:

```python
from nage.lang import lang

# Add French support
lang.add_language("fr", {
    "help": "Aide",
    "error": "Erreur",
    "configuration_required": "Configuration requise",
    # ... more translations
})
```

## Development

To run the project in development mode:

```bash
# Install in development mode
uv pip install -e .

# Run the CLI
python -m nage

# Run with uv
uv run python -m nage
```

### Project Structure
```
nage/
├── __init__.py
├── __main__.py      # Entry point
├── main.py          # CLI interface
├── config.py        # Configuration management
├── ai_client.py     # AI API client
└── lang.py          # Language management system
```

### Language Management Architecture
The project uses a centralized language management system (`lang.py`) that:
- Provides a clean API for text retrieval: `lang.get("key")`
- Supports dynamic language switching: `lang.set_language("zh")`
- Enables easy extension: `lang.add_language("code", translations)`
- Maintains type safety with proper annotations

## API Compatibility

Nage supports multiple AI API providers:

### Supported APIs:
- **DeepSeek API**: `https://api.deepseek.com/chat/completions`
- **OpenAI API**: `https://api.openai.com/v1/chat/completions`
- **Moonshot API**: `https://api.moonshot.cn/v1/chat/completions`
- **Zhipu API**: `https://open.bigmodel.cn/api/paas/v4/chat/completions`

### Preset Aliases:
```bash
nage --set-api=deepseek       # DeepSeek API
nage --set-api=openai         # OpenAI API
nage --set-api=moonshot       # Moonshot API
nage --set-api=zhipu          # Zhipu API
```

### API Key Requirements:
- DeepSeek: Get your API key from [DeepSeek Platform](https://platform.deepseek.com/)
- OpenAI: Get your API key from [OpenAI Platform](https://platform.openai.com/)
- Other providers: Check their respective documentation

### Common Issues:
- **404 Error**: Make sure to use the complete endpoint URL (e.g., `/chat/completions`)
- **401 Error**: Check your API key configuration with `nage --set`
- **Rate Limit**: Some APIs have usage limits, try again later
- **Language Issues**: Switch language with `nage --set-lang=en` or `nage --set-lang=zh`

## Contributing

We welcome contributions! Here's how you can help:

1. **Add new languages:** Extend the language support by adding translations
2. **Improve AI prompts:** Enhance the system prompts for better command suggestions
3. **Add new API providers:** Support additional AI API endpoints
4. **Bug fixes and improvements:** General code improvements and bug fixes

### Adding a New Language
1. Edit `nage/lang.py`
2. Add your language to the `translations` dictionary
3. Update `get_supported_languages()` method
4. Test with `nage --set-lang=your_language_code`

## Roadmap

- [ ] Support for more AI providers (Anthropic, Cohere, etc.)
- [ ] Plugin system for custom commands
- [ ] Command history and favorites
- [ ] Shell integration (bash/zsh completions)
- [ ] More language support (Spanish, French, Japanese, etc.)
- [ ] Configuration validation and migration tools

## License

This project is open source and available under the MIT License.