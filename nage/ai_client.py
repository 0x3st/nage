"""
AI client for communicating with AI API
"""

import requests
import json
from typing import Dict, Any, List
from .config import Config


class AIClient:
    """Client for communicating with AI API"""
    
    def __init__(self, config: Config):
        self.config = config
        self.default_model = config.get_model()  # Get model from config
        
    def send_request(self, prompt: str) -> Dict[str, Any]:
        """Send a request to the AI API"""
        api_endpoint = self.config.get_api_endpoint()
        api_key = self.config.get_api_key()
        
        if not api_endpoint or not api_key:
            raise RuntimeError("API endpoint and key must be configured first")
        
        # 验证端点格式
        if not self.config.validate_endpoint(api_endpoint):
            raise RuntimeError(f"Invalid API endpoint format: {api_endpoint}")
        
        # Prepare the request
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        # Get language setting for system prompt
        language = self.config.get_language()
        
        # System prompts in different languages
        system_prompts = {
            "zh": """你是一个有用的助手，专门提供终端命令建议。

你的回复应该是以下格式的 JSON：
{
    "commands": ["命令1", "命令2", ...],
    "description": "对这些命令的简要描述",
    "requires_input": false,
    "input_prompts": [],
    "warnings": ["警告1", "警告2", ...] (可选)
}

如果需要用户提供更多信息，请设置 "requires_input" 为 true 并在 "input_prompts" 中提供需要询问的问题。

专注于实用、安全的命令。始终提供实际可执行的命令，而不是解释说明。""",
            "en": """You are a helpful assistant that provides terminal command suggestions. 
                    
Your response should be structured as JSON with the following format:
{
    "commands": ["command1", "command2", ...],
    "description": "Brief description of what these commands do",
    "requires_input": false,
    "input_prompts": [],
    "warnings": ["warning1", "warning2", ...] (optional)
}

If you need more information from the user, set "requires_input" to true and provide "input_prompts" with questions to ask.

Focus on practical, safe commands. Always provide actual executable commands, not explanations."""
        }
        
        # Generic payload structure that should work with most AI APIs
        payload: Dict[str, Any] = {
            "model": self.default_model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompts.get(language, system_prompts["zh"])
                },
                {
                    "role": "user", 
                    "content": prompt
                }
            ],
            "max_tokens": 500,
            "temperature": 0.7
        }
        
        try:
            response = requests.post(
                api_endpoint,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                # Handle OpenAI-style response
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"].strip()
                    return self._parse_ai_response(content)
                # Handle DeepSeek-style response
                elif "choices" in result and len(result["choices"]) > 0 and "text" in result["choices"][0]:
                    content = result["choices"][0]["text"].strip()
                    return self._parse_ai_response(content)
                # Handle other response formats
                elif "response" in result:
                    content = result["response"].strip()
                    return self._parse_ai_response(content)
                elif "content" in result:
                    content = result["content"].strip()
                    return self._parse_ai_response(content)
                else:
                    return {"error": f"Sorry, I couldn't understand the response format. Response: {result}"}
            else:
                # 详细的错误处理
                error_msg = self._format_api_error(response)
                raise RuntimeError(error_msg)
                
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to connect to AI API: {e}")
        except json.JSONDecodeError:
            raise RuntimeError("Invalid JSON response from AI API")
    
    def _format_api_error(self, response: requests.Response) -> str:
        """Format API error message with helpful suggestions"""
        status_code = response.status_code
        
        # 常见错误码的建议
        error_suggestions = {
            400: "请检查请求格式和参数",
            401: "请检查 API 密钥是否正确",
            403: "API 密钥没有权限，请检查账户状态",
            404: "API 端点不存在，请检查端点配置",
            429: "请求过于频繁，请稍后再试",
            500: "服务器内部错误，请稍后再试"
        }
        
        suggestion = error_suggestions.get(status_code, "请检查网络连接和配置")
        
        try:
            error_data = response.json()
            if "error" in error_data:
                detail = error_data["error"]
                if isinstance(detail, dict) and "message" in detail:
                    detail = detail["message"] # type: ignore
                return f"API 错误 {status_code}: {detail}\n建议: {suggestion}"
            elif "error_msg" in error_data:
                return f"API 错误 {status_code}: {error_data['error_msg']}\n建议: {suggestion}"
            elif "message" in error_data:
                return f"API 错误 {status_code}: {error_data['message']}\n建议: {suggestion}"
            else:
                return f"API 错误 {status_code}: {response.text}\n建议: {suggestion}"
        except json.JSONDecodeError:
            return f"API 错误 {status_code}: {response.text}\n建议: {suggestion}"
    
    def _parse_ai_response(self, content: str) -> Dict[str, Any]:
        """Parse AI response and extract commands"""
        try:
            # Try to parse as JSON first
            response_data = json.loads(content)
            if isinstance(response_data, dict):
                return response_data # type: ignore
        except json.JSONDecodeError:
            pass
        
        # If not JSON, try to extract commands from text
        lines = content.strip().split('\n')
        commands: List[str] = []
        description = ""
        in_code_block = False
        
        for line in lines:
            line = line.strip()
            
            # Handle code blocks (```bash, ```, etc.)
            if line.startswith('```'):
                in_code_block = not in_code_block
                continue
            
            # If we're in a code block, treat non-empty lines as commands
            if in_code_block:
                if line and not line.startswith('#'):  # Skip empty lines and comments
                    commands.append(line)
                continue
            
            # Handle backticks format
            if line.startswith('`') and line.endswith('`'):
                command = line[1:-1]
                if command:
                    commands.append(command)
                continue
            
            # Handle $ prefix format
            if line.startswith('$ '):
                command = line[2:]
                if command:
                    commands.append(command)
                continue
            
            # Handle plain text commands (heuristic approach)
            if line and self._looks_like_command(line):
                commands.append(line)
        
        # If no commands found, treat as description
        if not commands:
            description = content
        
        return {
            "commands": commands,
            "description": description or "Commands to execute",
            "requires_input": False,
            "input_prompts": [],
            "warnings": []
        }
    
    def _looks_like_command(self, line: str) -> bool:
        """Heuristic to determine if a line looks like a command"""
        # Skip obvious non-commands
        if not line or line.startswith('#') or line.startswith('//'):
            return False
        
        # Skip lines that look like explanations or descriptions
        if any(line.lower().startswith(prefix) for prefix in [
            'here', 'to ', 'you can', 'this will', 'the ', 'for ', 'now ', 'next', 'first',
            'second', 'then', 'after', 'before', 'note:', 'tip:', 'warning:', 'example:',
            'try these', 'use these', 'run these', 'execute these', 'this is', 'it is',
            'they are', 'there are', 'you need', 'it requires', 'understanding of',
            'these will', 'these are', 'this helps', 'this shows'
        ]):
            return False
        
        # Skip lines that end with colons (likely headers/explanations)
        if line.endswith(':'):
            return False
        
        # Skip lines that are too long to be simple commands (likely explanations)
        if len(line) > 100:
            return False
        
        # Skip lines that contain too many common English words (likely explanations)
        common_words = ['is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 
                       'do', 'does', 'did', 'will', 'would', 'should', 'could', 'may', 'might',
                       'must', 'shall', 'can', 'cannot', 'very', 'too', 'also', 'just', 'only',
                       'about', 'into', 'over', 'under', 'some', 'many', 'most', 'all', 'any',
                       'both', 'each', 'few', 'more', 'other', 'such', 'what', 'which', 'who',
                       'when', 'where', 'why', 'how', 'important', 'concepts', 'remember',
                       'understand', 'requires', 'understanding', 'organized', 'hierarchically',
                       'help', 'helps', 'identify', 'check', 'usage', 'large', 'files', 'disk',
                       'give', 'overview', 'system', 'good', 'commands', 'your', 'you']
        
        words = line.lower().split()
        common_word_count = sum(1 for word in words if word in common_words)
        # If more than 40% of words are common English words, likely an explanation
        if len(words) > 4 and common_word_count / len(words) > 0.4:
            return False
        
        # Common command patterns
        command_patterns = [
            # Basic commands
            r'^(ls|pwd|cd|mkdir|rmdir|rm|cp|mv|cat|less|more|head|tail|grep|find|which|whereis)[\s]',
            # Advanced commands
            r'^(sudo|su|chmod|chown|chgrp|ps|kill|killall|jobs|bg|fg|nohup|screen|tmux)[\s]',
            # Network commands
            r'^(ping|wget|curl|ssh|scp|rsync|netstat|ss|lsof)[\s]',
            # Archive commands
            r'^(tar|gzip|gunzip|zip|unzip|7z)[\s]',
            # Git commands
            r'^git[\s]',
            # Package managers
            r'^(apt|yum|dnf|pacman|pip|npm|yarn|brew)[\s]',
            # System commands
            r'^(systemctl|service|mount|umount|df|du|free|top|htop|iotop)[\s]',
            # Text editors
            r'^(vim|nano|emacs|code)[\s]',
            # Docker commands
            r'^docker[\s]',
            # Python commands
            r'^python[\s]',
            # Simple commands without spaces
            r'^(ls|pwd|whoami|date|uptime|history|clear|exit|logout)$'
        ]
        
        import re
        for pattern in command_patterns:
            if re.match(pattern, line, re.IGNORECASE):
                return True
        
        # If line contains common command structures
        if any(char in line for char in ['|', '>', '<', '&&', '||', ';']):
            return True
        
        # If line starts with a word followed by space and arguments
        parts = line.split()
        if len(parts) >= 2 and not any(char in parts[0] for char in ['.', ':', '?', '!']):
            return True
        
        return False
