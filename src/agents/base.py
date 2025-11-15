"""Base agent interface"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime
import json

from src.data.storage import Database, AgentLogModel
from src.utils.logging import get_logger, AgentInteractionLogger
from src.utils.config import config


class BaseAgent(ABC):
    """Base class for all agents"""
    
    def __init__(self, name: str, db: Optional[Database] = None):
        """Initialize agent"""
        self.name = name
        self.db = db
        self.logger = get_logger(f"agents.{name}")
        self.interaction_logger = AgentInteractionLogger(self.logger)
        self.config = config.get_agents_config().get(name.lower(), {})
    
    def log_action(self, action: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Log agent action to database"""
        if self.db:
            session = self.db.get_session()
            try:
                log_entry = AgentLogModel(
                    agent_name=self.name,
                    timestamp=datetime.now(),
                    action=action,
                    data_json=data
                )
                session.add(log_entry)
                session.commit()
            except Exception as e:
                self.logger.error(f"Failed to log action: {e}")
                session.rollback()
            finally:
                session.close()
    
    def log_info(self, message: str, **kwargs) -> None:
        """Log info message"""
        self.logger.info(message, **kwargs)
        self.log_action("info", {"message": message, **kwargs})
    
    def log_error(self, message: str, **kwargs) -> None:
        """Log error message"""
        self.logger.error(message, **kwargs)
        self.log_action("error", {"message": message, **kwargs})
    
    def log_warning(self, message: str, **kwargs) -> None:
        """Log warning message"""
        self.logger.warning(message, **kwargs)
        self.log_action("warning", {"message": message, **kwargs})
    
    @abstractmethod
    def process(self, *args, **kwargs) -> Any:
        """Process method to be implemented by each agent"""
        pass
    
    def is_enabled(self) -> bool:
        """Check if agent is enabled"""
        return self.config.get('enabled', True)
    
    def validate_input(self, data: Any, expected_type: type) -> bool:
        """Validate input data type"""
        if not isinstance(data, expected_type):
            self.log_error(f"Invalid input type: expected {expected_type}, got {type(data)}")
            return False
        return True

