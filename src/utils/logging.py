"""Logging configuration"""

import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional, Any
from datetime import datetime


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    log_dir: str = "data/logs"
) -> logging.Logger:
    """Set up logging configuration"""
    
    # Create log directory if it doesn't exist
    if log_file:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
    
    # Configure root logger
    logger = logging.getLogger("terrarium")
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Console handler with enhanced format for agent interactions
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level.upper()))
    console_format = logging.Formatter(
        '%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        file_path = Path(log_dir) / log_file
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5
        )
        file_handler.setLevel(getattr(logging, log_level.upper()))
        file_format = logging.Formatter(
            '%(asctime)s | %(name)-30s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module"""
    return logging.getLogger(f"terrarium.{name}")


class AgentInteractionLogger:
    """Logger for agent interactions and handoffs"""
    
    def __init__(self, logger: logging.Logger):
        """Initialize interaction logger"""
        self.logger = logger
    
    def log_handoff(self, from_agent: str, to_agent: str, data_type: str, count: int = 0):
        """Log handoff between agents"""
        self.logger.info(
            f"🔄 HANDOFF: {from_agent} → {to_agent} | "
            f"Type: {data_type} | Count: {count}"
        )
    
    def log_agent_start(self, agent_name: str, input_summary: str = ""):
        """Log agent starting work"""
        self.logger.info(
            f"▶️  AGENT START: {agent_name} | {input_summary}"
        )
    
    def log_agent_complete(self, agent_name: str, output_summary: str = ""):
        """Log agent completing work"""
        self.logger.info(
            f"✅ AGENT COMPLETE: {agent_name} | {output_summary}"
        )
    
    def log_revision_request(self, from_agent: str, to_agent: str, reason: str):
        """Log revision request"""
        self.logger.warning(
            f"🔁 REVISION REQUEST: {from_agent} → {to_agent} | Reason: {reason}"
        )
    
    def log_decision(self, agent_name: str, decision: str, details: str = ""):
        """Log agent decision"""
        self.logger.info(
            f"🎯 DECISION: {agent_name} | {decision} | {details}"
        )


def log_data_object(logger: logging.Logger, obj_name: str, obj: Any, max_depth: int = 10) -> None:
    """Log a data object in a readable format for debugging
    
    Args:
        logger: Logger instance
        obj_name: Name/description of the object
        obj: Object to log
        max_depth: Maximum depth for nested structures (default: 5)
    """
    from src.utils.config import config
    if not config.is_debug_mode():
        return
    
    import json
    from dataclasses import asdict, is_dataclass
    from enum import Enum
    from datetime import datetime, date
    
    def make_serializable(o: Any, depth: int = 0) -> Any:
        """Recursively convert object to JSON-serializable format"""
        if depth > max_depth:
            return f"<max depth {max_depth} reached>"
        
        # Handle None
        if o is None:
            return None
        
        # Handle simple types first (before checking for __dict__)
        if isinstance(o, (str, int, float, bool)):
            return o
        elif isinstance(o, (datetime, date)):
            return o.isoformat()
        elif isinstance(o, Enum):
            # Enums should be converted to their value (they're str enums)
            return o.value if hasattr(o, 'value') else str(o)
        elif is_dataclass(o):
            return make_serializable(asdict(o), depth + 1)
        elif isinstance(o, (list, tuple)):
            return [make_serializable(item, depth + 1) for item in o[:10]]  # Limit to 10 items
        elif isinstance(o, dict):
            return {k: make_serializable(v, depth + 1) for k, v in list(o.items())[:20]}  # Limit to 20 keys
        elif hasattr(o, '__dict__'):
            # Only serialize __dict__ if it's not an Enum (already handled above)
            if isinstance(o, Enum):
                return o.value if hasattr(o, 'value') else str(o)
            return make_serializable(o.__dict__, depth + 1)
        else:
            return str(o)[:500]  # Limit string length
    
    try:
        serializable = make_serializable(obj)
        json_str = json.dumps(serializable, indent=2, default=str)
        logger.debug(f"🔍 DEBUG DATA: {obj_name}\n{json_str}")
    except Exception as e:
        logger.debug(f"🔍 DEBUG DATA: {obj_name} (serialization failed: {e})\n{str(obj)[:1000]}")

