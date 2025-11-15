"""Configuration management"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


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


# Global config instance
config = Config()

