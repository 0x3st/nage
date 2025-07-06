"""
Configuration management for Nage
"""
import json
from pathlib import Path
from typing import Optional, Dict, Any


class Config:
    """Configuration manager for Nage"""
    
    # Preset API endpoints
    PRESET_ENDPOINTS = {
        "deepseek": "https://api.deepseek.com/chat/completions",
        "openai": "https://api.openai.com/v1/chat/completions",
        "anthropic": "https://api.anthropic.com/v1/messages",
        "moonshot": "https://api.moonshot.cn/v1/chat/completions",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    }
    
    # Supported languages
    SUPPORTED_LANGUAGES = {
        "en": "English",
        "zh": "Chinese"
    }
    
    # Default models
    DEFAULT_MODELS = {
        "deepseek": "deepseek-chat",
        "openai": "gpt-3.5-turbo",
        "anthropic": "claude-3-haiku-20240307",
        "moonshot": "moonshot-v1-8k",
        "zhipu": "glm-4"
    }
    
    def __init__(self):
        self.config_dir = Path.home() / ".nage"
        self.config_file = self.config_dir / "config.json"
        self.ensure_config_dir()
        
    def ensure_config_dir(self):
        """Ensure the configuration directory exists"""
        self.config_dir.mkdir(exist_ok=True)
        
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file"""
        if not self.config_file.exists():
            return {}
        
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    
    def save_config(self, config: Dict[str, Any]):
        """Save configuration to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except IOError as e:
            raise RuntimeError(f"Failed to save configuration: {e}")
    
    def get_api_endpoint(self) -> Optional[str]:
        """Get API endpoint from config"""
        config = self.load_config()
        return config.get("api_endpoint")
    
    def set_api_endpoint(self, endpoint: str):
        """Set API endpoint in config"""
        # Check if it's a preset endpoint alias
        if endpoint.lower() in self.PRESET_ENDPOINTS:
            endpoint = self.PRESET_ENDPOINTS[endpoint.lower()]
        
        config = self.load_config()
        config["api_endpoint"] = endpoint
        self.save_config(config)
    
    def get_api_key(self) -> Optional[str]:
        """Get API key from config"""
        config = self.load_config()
        return config.get("api_key")
    
    def set_api_key(self, key: str):
        """Set API key in config"""
        config = self.load_config()
        config["api_key"] = key
        self.save_config(config)
    
    def get_language(self) -> str:
        """Get language setting from config"""
        config = self.load_config()
        return config.get("language", "en")  # Default to English
    
    def set_language(self, language: str):
        """Set language setting in config"""
        if language not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language: {language}. Supported: {list(self.SUPPORTED_LANGUAGES.keys())}")
        
        config = self.load_config()
        config["language"] = language
        self.save_config(config)
    
    def get_model(self) -> str:
        """Get model setting from config"""
        config = self.load_config()
        return config.get("model", "deepseek-chat")  # Default model
    
    def set_model(self, model: str):
        """Set model setting in config"""
        config = self.load_config()
        config["model"] = model
        self.save_config(config)
    
    def get_default_model_for_endpoint(self, endpoint: str) -> str:
        """Get default model for a given endpoint"""
        for name, url in self.PRESET_ENDPOINTS.items():
            if url == endpoint:
                return self.DEFAULT_MODELS.get(name, "deepseek-chat")
        return "deepseek-chat"
    
    def is_configured(self) -> bool:
        """Check if both API endpoint and key are configured"""
        return self.get_api_endpoint() is not None and self.get_api_key() is not None
    
    def get_preset_endpoints(self) -> Dict[str, str]:
        """Get available preset endpoints"""
        return self.PRESET_ENDPOINTS.copy()
    
    def get_supported_languages(self) -> Dict[str, str]:
        """Get supported languages"""
        return self.SUPPORTED_LANGUAGES.copy()
    
    def validate_endpoint(self, endpoint: str) -> bool:
        """Validate if endpoint looks like a valid URL"""
        import re
        url_pattern = r'^https?://[^\s/$.?#].[^\s]*$'
        return bool(re.match(url_pattern, endpoint))
