"""Base agent interface"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime, date
from enum import Enum
import json
from dataclasses import asdict, is_dataclass

from src.data.storage import Database, AgentLogModel
from src.utils.logging import get_logger, AgentInteractionLogger
from src.utils.config import config
from src.utils.llm import LLMClient, get_llm_client


def _make_json_serializable(obj: Any) -> Any:
    """
    Recursively convert dataclasses, enums, and dates to JSON-serializable types
    
    Args:
        obj: Object to convert
        
    Returns:
        JSON-serializable version of the object
    """
    if is_dataclass(obj):
        return {k: _make_json_serializable(v) for k, v in asdict(obj).items()}
    elif isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, (date, datetime)):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_make_json_serializable(item) for item in obj]
    else:
        return obj


class BaseAgent(ABC):
    """Base class for all agents"""
    
    def __init__(self, name: str, db: Optional[Database] = None, llm_client: Optional[LLMClient] = None):
        """Initialize agent"""
        self.name = name
        self.db = db
        self.logger = get_logger(f"agents.{name}")
        self.interaction_logger = AgentInteractionLogger(self.logger)
        self.config = config.get_agents_config().get(name.lower(), {})
        # Get agent-specific LLM client if not provided
        self.llm_client = llm_client or get_llm_client(agent_name=name)
        self.system_prompt = self._get_system_prompt()
        # Log which model is being used (at INFO level so it's visible)
        self.logger.info(f"🤖 Agent '{self.name}' initialized with model: {self.llm_client.model}")
    
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
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for this agent - override in subclasses"""
        return ""
    
    def call_llm(
        self,
        user_prompt: str,
        input_data: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        parse_json: bool = True
    ) -> Dict[str, Any]:
        """
        Call LLM with agent's system prompt
        
        Args:
            user_prompt: User prompt describing the task
            input_data: Optional input data to include in prompt
            temperature: Sampling temperature
            parse_json: Whether to parse response as JSON
            
        Returns:
            LLM response (parsed JSON if parse_json=True)
        """
        if not self.system_prompt:
            raise ValueError(f"Agent {self.name} has no system prompt defined")
        
        # Format user prompt with input data if provided
        if input_data:
            # Convert dataclasses and other non-serializable types to JSON-compatible format
            serializable_data = _make_json_serializable(input_data)
            formatted_prompt = f"""{user_prompt}

Input data:
{json.dumps(serializable_data, indent=2)}"""
        else:
            formatted_prompt = user_prompt
        
        self.logger.debug(f"Calling LLM for {self.name}")
        
        # Get usage stats before call
        usage_before = self.llm_client.get_usage_stats()
        
        response = self.llm_client.call(
            system_prompt=self.system_prompt,
            user_prompt=formatted_prompt,
            temperature=temperature,
            parse_json=parse_json
        )
        
        # Get usage stats after call and log delta
        usage_after = self.llm_client.get_usage_stats()
        tokens_used = usage_after["total_tokens"] - usage_before["total_tokens"]
        prompt_tokens = usage_after["prompt_tokens"] - usage_before["prompt_tokens"]
        completion_tokens = usage_after["completion_tokens"] - usage_before["completion_tokens"]
        
        if tokens_used > 0:
            self.logger.info(
                f"💰 {self.name} token usage: "
                f"{tokens_used:,} total ({prompt_tokens:,} prompt + {completion_tokens:,} completion)"
            )
        
        return response

