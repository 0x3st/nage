"""
Language management for Nage
"""
from typing import Dict, Optional


class LanguageManager:
    """Manages language strings and translations"""
    
    def __init__(self):
        self.current_language = "en"
        self.translations = {
            "en": {
                # Configuration messages
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
                "asking_ai": "Asking AI...",
                
                # Configuration UI
                "current_configuration": "Current Configuration",
                "api_endpoint": "API Endpoint",
                "api_key": "API Key",
                "model": "Model",
                "language": "Language",
                "config_file": "Config File",
                "available_preset_endpoints": "Available Preset Endpoints",
                "supported_languages": "Supported Languages",
                "current": "current",
                "status": "Status",
                "ready_to_use": "Ready to use",
                "setup_required": "Setup required",
                "not_configured": "Not configured",
                "configured": "Configured",
                "usage_examples": "Usage examples",
                
                # Help messages
                "help_title": "Help",
                "help_usage": "Usage",
                "help_examples": "Examples",
                "configuration_required": "Configuration Required",
                "setup_required_msg": "Please configure your API endpoint and key first",
                "setup_step1": "Set API endpoint",
                "setup_step2": "Set API key",
                "config_stored_msg": "Your configuration is stored in ~/.nage/config.json",
                
                # CLI descriptions
                "cli_description": "Nage - AI assisted terminal tool\n\nAsk AI for help with terminal commands and tools.",
                "version_info_title": "Version Information",
                "version_info_description": "AI-powered terminal command suggestions",
                "project_url": "Project URL",
                "license": "License",
                
                # Error messages
                "error": "Error",
                "api_error": "API Error",
                "check_config": "Please check your API configuration and try again.",
                "invalid_endpoint": "Invalid API endpoint format",
                "config_save_failed": "Failed to save configuration",
                "unsupported_language": "Unsupported language",
                "connection_failed": "Failed to connect to AI API",
                "invalid_json": "Invalid JSON response from AI API",
                "must_configure": "API endpoint and key must be configured first",
                
                # Success messages
                "api_endpoint_set": "API endpoint set to",
                "api_key_set": "API key has been set",
                "model_set": "Model set to",
                "language_set": "Language set to",
                
                # API error suggestions
                "check_request_format": "Please check request format and parameters",
                "check_api_key": "Please check if API key is correct",
                "no_permission": "API key has no permission, please check account status",
                "endpoint_not_found": "API endpoint does not exist, please check endpoint configuration",
                "too_many_requests": "Too many requests, please try again later",
                "server_error": "Server internal error, please try again later",
                "check_network": "Please check network connection and configuration",
                "suggestion": "Suggestion",
                
                # Language names
                "english": "English",
                "chinese": "Chinese",
                
                # Common actions
                "set": "Set",
                "show": "Show",
                "help": "Help",
                "version": "Version",
                "quit": "Quit",
                "yes": "Yes",
                "no": "No",
                "cancel": "Cancel",
                "continue": "Continue",
                "skip": "Skip",
                "selective": "Selective",
                
                # System prompts for AI
                "system_prompt": """You are a helpful assistant that provides terminal command suggestions. 
                    
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
            },
            "zh": {
                # Configuration messages
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
                "asking_ai": "正在询问AI...",
                
                # Configuration UI
                "current_configuration": "当前配置",
                "api_endpoint": "API 端点",
                "api_key": "API 密钥",
                "model": "模型",
                "language": "语言",
                "config_file": "配置文件",
                "available_preset_endpoints": "可用预设端点",
                "supported_languages": "支持的语言",
                "current": "当前",
                "status": "状态",
                "ready_to_use": "可以使用",
                "setup_required": "需要设置",
                "not_configured": "未配置",
                "configured": "已配置",
                "usage_examples": "使用示例",
                
                # Help messages
                "help_title": "帮助",
                "help_usage": "用法",
                "help_examples": "示例",
                "configuration_required": "需要配置",
                "setup_required_msg": "请先配置您的API端点和密钥",
                "setup_step1": "设置API端点",
                "setup_step2": "设置API密钥",
                "config_stored_msg": "您的配置存储在 ~/.nage/config.json",
                
                # CLI descriptions
                "cli_description": "Nage - AI 辅助终端工具\n\n询问AI以获取终端命令和工具的帮助。",
                "version_info_title": "版本信息",
                "version_info_description": "AI驱动的终端命令建议",
                "project_url": "项目网址",
                "license": "许可证",
                
                # Error messages
                "error": "错误",
                "api_error": "API 错误",
                "check_config": "请检查您的API配置后重试。",
                "invalid_endpoint": "无效的API端点格式",
                "config_save_failed": "保存配置失败",
                "unsupported_language": "不支持的语言",
                "connection_failed": "连接AI API失败",
                "invalid_json": "AI API返回的JSON无效",
                "must_configure": "必须先配置API端点和密钥",
                
                # Success messages
                "api_endpoint_set": "API端点已设置为",
                "api_key_set": "API密钥已设置",
                "model_set": "模型已设置为",
                "language_set": "语言已设置为",
                
                # API error suggestions
                "check_request_format": "请检查请求格式和参数",
                "check_api_key": "请检查API密钥是否正确",
                "no_permission": "API密钥没有权限，请检查账户状态",
                "endpoint_not_found": "API端点不存在，请检查端点配置",
                "too_many_requests": "请求过于频繁，请稍后再试",
                "server_error": "服务器内部错误，请稍后再试",
                "check_network": "请检查网络连接和配置",
                "suggestion": "建议",
                
                # Language names
                "english": "英语",
                "chinese": "中文",
                
                # Common actions
                "set": "设置",
                "show": "显示",
                "help": "帮助",
                "version": "版本",
                "quit": "退出",
                "yes": "是",
                "no": "否",
                "cancel": "取消",
                "continue": "继续",
                "skip": "跳过",
                "selective": "选择",
                
                # System prompts for AI
                "system_prompt": """你是一个有用的助手，专门提供终端命令建议。

你的回复应该是以下格式的 JSON：
{
    "commands": ["命令1", "命令2", ...],
    "description": "对这些命令的简要描述",
    "requires_input": false,
    "input_prompts": [],
    "warnings": ["警告1", "警告2", ...] (可选)
}

如果需要用户提供更多信息，请设置 "requires_input" 为 true 并在 "input_prompts" 中提供需要询问的问题。

专注于实用、安全的命令。始终提供实际可执行的命令，而不是解释说明。"""
            }
        }
    
    def set_language(self, language: str) -> None:
        """Set the current language"""
        if language not in self.translations:
            raise ValueError(f"Unsupported language: {language}")
        self.current_language = language
    
    def get_language(self) -> str:
        """Get the current language"""
        return self.current_language
    
    def get_supported_languages(self) -> Dict[str, str]:
        """Get supported languages with their display names"""
        return {
            "en": self.get("english"),
            "zh": self.get("chinese")
        }
    
    def get(self, key: str, default: Optional[str] = None) -> str:
        """Get a translated string for the current language"""
        if self.current_language not in self.translations:
            self.current_language = "en"
        
        language_strings = self.translations[self.current_language]
        return language_strings.get(key, default or key)
    
    def format(self, key: str, *args: str, **kwargs: str) -> str:
        """Get a translated string and format it with arguments"""
        text = self.get(key)
        if args or kwargs:
            return text.format(*args, **kwargs)
        return text
    
    def add_language(self, language_code: str, translations: Dict[str, str]) -> None:
        """Add a new language translation"""
        self.translations[language_code] = translations
    
    def add_translation(self, language_code: str, key: str, value: str) -> None:
        """Add a single translation to a language"""
        if language_code not in self.translations:
            self.translations[language_code] = {}
        self.translations[language_code][key] = value


# Global language manager instance
lang = LanguageManager()
