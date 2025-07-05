# Nage - AI Assisted Terminal Tool

Nage is a Python-based AI assisted tool that helps you when you forget terminal commands. Just ask AI for help with any terminal-related questions!

## Features

- AI-powered terminal command suggestions
- Easy configuration management
- Beautiful terminal output with rich formatting
- Secure API key storage
- Multi-language support (Chinese/English)
- Customizable AI models

## Installation

This project is managed by `uv`. To install and set up:

```bash
# Install dependencies
uv sync

# Install the package in development mode
uv pip install -e .
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
   ```

2. **Configure API key:**
   ```bash
   nage --set-key="api keys"
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

## Development

To run the project in development mode:

```bash
# Install in development mode
uv pip install -e .

# Run the CLI
python -m yao.main
```

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
- **401 Error**: Check your API key configuration
- **Rate Limit**: Some APIs have usage limits, try again later

## License

This project is open source and available under the MIT License.