"""
AI client for communicating with AI API
"""

import requests
import json
from typing import Dict, Any, List
from .config import Config
from .lang import lang


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
        
        # Validate endpoint format
        if not self.config.validate_endpoint(api_endpoint):
            raise RuntimeError(f"Invalid API endpoint format: {api_endpoint}")
        
        # Prepare the request
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        # Get language setting for system prompt
        language = self.config.get_language()
        lang.set_language(language)
        
        # Generic payload structure that should work with most AI APIs
        payload: Dict[str, Any] = {
            "model": self.default_model,
            "messages": [
                {
                    "role": "system",
                    "content": lang.get("system_prompt")
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
                # Detailed error handling
                error_msg = self._format_api_error(response)
                raise RuntimeError(error_msg)
                
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to connect to AI API: {e}")
        except json.JSONDecodeError:
            raise RuntimeError("Invalid JSON response from AI API")
    
    def _format_api_error(self, response: requests.Response) -> str:
        """Format API error message with helpful suggestions"""
        status_code = response.status_code
        
        # Common error code suggestions
        error_suggestions = {
            400: lang.get("check_request_format"),
            401: lang.get("check_api_key"),
            403: lang.get("no_permission"),
            404: lang.get("endpoint_not_found"),
            429: lang.get("too_many_requests"),
            500: lang.get("server_error")
        }
        
        suggestion = error_suggestions.get(status_code, lang.get("check_network"))
        
        try:
            error_data = response.json()
            if "error" in error_data:
                detail = error_data["error"]
                if isinstance(detail, dict) and "message" in detail:
                    detail = detail["message"] # type: ignore
                return f"{lang.get('api_error')} {status_code}: {detail}\n{lang.get('suggestion')}: {suggestion}"
            elif "error_msg" in error_data:
                return f"{lang.get('api_error')} {status_code}: {error_data['error_msg']}\n{lang.get('suggestion')}: {suggestion}"
            elif "message" in error_data:
                return f"{lang.get('api_error')} {status_code}: {error_data['message']}\n{lang.get('suggestion')}: {suggestion}"
            else:
                return f"{lang.get('api_error')} {status_code}: {response.text}\n{lang.get('suggestion')}: {suggestion}"
        except json.JSONDecodeError:
            return f"{lang.get('api_error')} {status_code}: {response.text}\n{lang.get('suggestion')}: {suggestion}"
    
    def _parse_ai_response(self, content: str) -> Dict[str, Any]:
        """Parse AI response and extract commands"""        
        try:
            # Try to parse as JSON first
            response_data = json.loads(content)
            if isinstance(response_data, dict):
                return response_data # type: ignore
        except json.JSONDecodeError:
            pass
        
        # Check if it's JSON contained in code blocks
        if content.strip().startswith('```json') or content.strip().startswith('```'):
            lines = content.strip().split('\n')
            json_lines: List[str] = []
            in_code_block = False
            
            for line in lines:
                if line.strip().startswith('```'):
                    in_code_block = not in_code_block
                    continue
                if in_code_block:
                    json_lines.append(line)
            
            if json_lines:
                json_content = '\n'.join(json_lines)
                try:
                    response_data = json.loads(json_content)
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
        
        # Skip lines that look like explanations or descriptions (stricter checking)
        explanation_prefixes = [
            'here are', 'to do this', 'you can use', 'this will help', 'the following',
            'for example', 'note that', 'tip:', 'warning:', 'example:', 'explanation:',
            'these commands', 'this command', 'this helps', 'this shows'
        ]
        
        line_lower = line.lower()
        if any(line_lower.startswith(prefix) for prefix in explanation_prefixes):
            return False
        
        # Skip lines that end with colons (likely headers/explanations)
        if line.endswith(':') and not line.endswith('::'):  # Allow :: cases
            return False
        
        # Skip lines that are too long to be simple commands (relaxed limit)
        if len(line) > 200:
            return False
        
        # First check if it matches common command patterns
        import re
        command_patterns = [
            # Basic commands (with and without arguments)
            r'^(ls|pwd|cd|mkdir|rmdir|rm|cp|mv|cat|less|more|head|tail|grep|find|which|whereis)(\s|$)',
            # Advanced commands
            r'^(sudo|su|chmod|chown|chgrp|ps|kill|killall|jobs|bg|fg|nohup|screen|tmux)(\s|$)',
            # Network commands
            r'^(ping|wget|curl|ssh|scp|rsync|netstat|ss|lsof)(\s|$)',
            # Archive commands
            r'^(tar|gzip|gunzip|zip|unzip|7z)(\s|$)',
            # Git commands
            r'^git(\s|$)',
            # Package managers
            r'^(apt|yum|dnf|pacman|pip|npm|yarn|brew)(\s|$)',
            # System commands
            r'^(systemctl|service|mount|umount|df|du|free|top|htop|iotop)(\s|$)',
            # Text editors
            r'^(vim|nano|emacs|code)(\s|$)',
            # Docker commands
            r'^docker(\s|$)',
            # Python commands
            r'^python(\s|$)',
            # Other common commands
            r'^(make|cmake|gcc|g\+\+|java|javac|node|ruby|go|rust)(\s|$)',
            # System administration
            r'^(uname|arch|hostname|whoami|id|groups)(\s|$)',
        ]
        
        for pattern in command_patterns:
            if re.match(pattern, line, re.IGNORECASE):
                return True
        
        # If it contains common command structure symbols, it's likely a command
        if any(char in line for char in ['|', '>', '<', '&&', '||', ';']):
            return True
        
        # Check if it looks like a command (simplified version)
        parts = line.split()
        if len(parts) >= 1:
            first_word = parts[0].lower()
            
            # If the first word doesn't contain special characters, it might be a command
            if not any(char in first_word for char in ['.', ':', '?', '!', ',', '(', ')']):
                # Simple heuristic: if the line doesn't contain too many common English explanation words, it might be a command
                explanation_indicators = [
                    'is', 'are', 'was', 'were', 'will be', 'would be', 'should be',
                    'can be', 'this is', 'that is', 'these are', 'those are',
                    'you need to', 'you should', 'you can', 'you will',
                    'it will', 'it can', 'it should', 'this will', 'that will'
                ]
                
                line_text = ' ' + line_lower + ' '  # Add spaces to match complete phrases
                explanation_count = sum(1 for indicator in explanation_indicators if indicator in line_text)
                
                # If there aren't too many explanatory words, and it's not obviously a sentence, it might be a command
                if explanation_count == 0 and not line.endswith('.'):
                    return True
        
        return False
