"""Configuration management"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from src.utils.logging import get_logger

# Load environment variables
load_dotenv()

logger = get_logger("utils.config")


class Config:
    """Configuration manager"""
    
    def __init__(self, config_path: str = "config/config.yaml"):
        """Initialize configuration"""
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self.load()
    
    def load(self) -> None:
        """Load configuration from YAML file"""
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                self._config = yaml.safe_load(f) or {}
        else:
            self._config = {}
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation"""
        keys = key.split('.')
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value
    
    def get_bankroll_config(self) -> Dict[str, Any]:
        """Get bankroll configuration"""
        return self._config.get('bankroll', {})
    
    def get_betting_config(self) -> Dict[str, Any]:
        """Get betting configuration"""
        return self._config.get('betting', {})
    
    def get_scraping_config(self) -> Dict[str, Any]:
        """Get scraping configuration"""
        return self._config.get('scraping', {})
    
    def get_kenpom_credentials(self) -> Optional[Dict[str, str]]:
        """Get KenPom credentials from environment variables"""
        email = os.getenv('KENPOM_EMAIL')
        password = os.getenv('KENPOM_PASSWORD')
        
        if email and password:
            return {'email': email, 'password': password}
        return None
    
    def is_kenpom_enabled(self) -> bool:
        """Check if KenPom scraping is enabled"""
        scraping_config = self.get_scraping_config()
        kenpom_config = scraping_config.get('kenpom', {})
        return kenpom_config.get('enabled', True) and self.get_kenpom_credentials() is not None
    
    def get_agents_config(self) -> Dict[str, Any]:
        """Get agents configuration"""
        return self._config.get('agents', {})
    
    def get_scheduler_config(self) -> Dict[str, Any]:
        """Get scheduler configuration"""
        return self._config.get('scheduler', {})
    
    def get_database_url(self) -> str:
        """Get database URL from environment or config"""
        return os.getenv('DATABASE_URL', 'sqlite:///data/db/terrarium.db')
    
    def get_log_level(self) -> str:
        """Get log level"""
        return os.getenv('LOG_LEVEL', 'INFO')
    
    def is_debug_mode(self) -> bool:
        """Check if debug mode is enabled"""
        return os.getenv('DEBUG', '').lower() in ('true', '1', 'yes') or self.get('debug', False)
    
    def get_llm_config(self) -> Dict[str, Any]:
        """Get LLM configuration"""
        return self._config.get('llm', {})
    
    def get_agent_model(self, agent_name: str) -> str:
        """Get model for a specific agent"""
        llm_config = self.get_llm_config()
        agent_models = llm_config.get('agent_models', {})
        # Use agent-specific model if available, otherwise default
        model_name = agent_models.get(agent_name.lower(), llm_config.get('model', 'gpt-4o-mini'))
        
        logger.debug(f"📋 Model config for '{agent_name}': '{model_name}'")
        
        return model_name


# Global config instance
config = Config()

