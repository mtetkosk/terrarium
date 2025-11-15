"""Logging configuration"""

import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional
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

