"""LLM client utilities for OpenAI API"""

import json
import os
from typing import Optional, Dict, Any, List, Callable
from openai import OpenAI
from src.utils.logging import get_logger

logger = get_logger("utils.llm")


class LLMClient:
    """Client for OpenAI API calls"""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        # Note: Available models include:
        # - gpt-4o-mini (cheapest, fast, good for most tasks) - ~$0.15/$0.60 per 1M tokens
        # - gpt-4o (more capable, slightly more expensive) - ~$2.50/$10 per 1M tokens
        # - gpt-4-turbo (very capable, more expensive) - ~$10/$30 per 1M tokens
        """
        Initialize LLM client
        
        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model to use (default: gpt-4o-mini)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required. Set OPENAI_API_KEY env var or pass api_key parameter.")
        
        self.client = OpenAI(api_key=self.api_key)
        self.model = model
        self.logger = logger
        # Token usage tracking
        self.total_tokens_used = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
    
    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        parse_json: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Make LLM API call
        
        Args:
            system_prompt: System prompt (agent instructions)
            user_prompt: User prompt (task-specific input)
            response_format: Optional response format specification
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response
            parse_json: Whether to parse response as JSON
            
        Returns:
            Parsed response (JSON dict if parse_json=True, else raw text)
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        kwargs = {
            "model": self.model,
            "messages": messages,
        }
        
        # Only add temperature if model is not gpt-5 (gpt-5 models don't support temperature)
        if not self.model.startswith("gpt-5"):
            kwargs["temperature"] = temperature
        
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        
        # If response_format is specified, use structured output
        if response_format:
            kwargs["response_format"] = response_format
        
        # Add tools if provided
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        
        try:
            self.logger.debug(f"Calling {self.model} with {len(system_prompt)} char system prompt")
            response = self.client.chat.completions.create(**kwargs)
            
            # Extract and log token usage
            usage = response.usage
            if usage:
                prompt_tokens = usage.prompt_tokens or 0
                completion_tokens = usage.completion_tokens or 0
                total_tokens = usage.total_tokens or 0
                
                # Track totals
                self.total_prompt_tokens += prompt_tokens
                self.total_completion_tokens += completion_tokens
                self.total_tokens_used += total_tokens
                
                # Log token usage
                self.logger.info(
                    f"📊 Token usage ({self.model}): "
                    f"Prompt: {prompt_tokens:,} | "
                    f"Completion: {completion_tokens:,} | "
                    f"Total: {total_tokens:,}"
                )
            
            # Handle tool calls if present
            message = response.choices[0].message
            if message.tool_calls:
                # Return tool calls for the caller to handle
                # Also return the message content if any
                result = {
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in message.tool_calls
                    ]
                }
                if message.content:
                    result["content"] = message.content
                return result
            
            content = message.content
            
            if parse_json:
                try:
                    # Try to parse as JSON
                    parsed = json.loads(content)
                    return parsed
                except json.JSONDecodeError as e:
                    # Log the error with context
                    self.logger.warning(f"JSON parsing error: {e}")
                    self.logger.debug(f"Response content (first 500 chars): {content[:500]}")
                    
                    # Try to extract JSON from markdown code blocks
                    json_str = None
                    if "```json" in content:
                        json_start = content.find("```json") + 7
                        json_end = content.find("```", json_start)
                        if json_end > json_start:
                            json_str = content[json_start:json_end].strip()
                    elif "```" in content:
                        json_start = content.find("```") + 3
                        json_end = content.find("```", json_start)
                        if json_end > json_start:
                            json_str = content[json_start:json_end].strip()
                    
                    if json_str:
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError as e2:
                            self.logger.warning(f"Failed to parse extracted JSON: {e2}")
                    
                    # Try to repair common JSON issues
                    try:
                        repaired = self._repair_json(content)
                        if repaired:
                            return json.loads(repaired)
                    except Exception as repair_error:
                        self.logger.debug(f"JSON repair failed: {repair_error}")
                    
                    # Last resort: try to find JSON object in the content
                    try:
                        # Look for first { and last }
                        first_brace = content.find('{')
                        last_brace = content.rfind('}')
                        if first_brace >= 0 and last_brace > first_brace:
                            json_candidate = content[first_brace:last_brace+1]
                            return json.loads(json_candidate)
                    except Exception:
                        pass
                    
                    # If all else fails, return raw content with error info
                    self.logger.error(
                        f"Failed to parse JSON response after all attempts. "
                        f"Error: {e}. Returning raw response."
                    )
                    self.logger.debug(f"Full response content:\n{content}")
                    return {
                        "raw_response": content,
                        "parse_error": str(e),
                        "error_type": "json_decode_error"
                    }
            else:
                return {"raw_response": content}
                
        except Exception as e:
            error_msg = str(e)
            # Check for specific OpenAI API errors
            if "insufficient_quota" in error_msg or "429" in error_msg:
                self.logger.error(
                    "OpenAI API Quota Error: Your API account has no credits or has exceeded usage limits.\n"
                    "NOTE: ChatGPT Plus subscription does NOT include API access.\n"
                    "You need to:\n"
                    "1. Go to https://platform.openai.com/account/billing\n"
                    "2. Add payment method and purchase API credits\n"
                    "3. Check your usage limits at https://platform.openai.com/account/limits"
                )
            elif "rate_limit" in error_msg.lower():
                self.logger.warning("OpenAI API Rate Limit: Too many requests. Will retry...")
            else:
                self.logger.error(f"Error calling LLM: {e}", exc_info=True)
            raise
    
    def call_agent(
        self,
        agent_name: str,
        system_prompt: str,
        input_data: Dict[str, Any],
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """
        Call an agent with structured input/output
        
        Args:
            agent_name: Name of agent (for logging)
            system_prompt: Agent's system prompt
            input_data: Input data as dict (will be formatted as JSON)
            temperature: Sampling temperature
            
        Returns:
            Agent response as dict
        """
        user_prompt = f"""Please process the following input data and provide your response in the specified JSON format.

Input data:
{json.dumps(input_data, indent=2)}

Remember to follow your role and responsibilities as defined in your system prompt."""
        
        self.logger.info(f"Calling {agent_name} agent")
        response = self.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            parse_json=True
        )
        
        return response
    
    def get_usage_stats(self) -> Dict[str, int]:
        """Get cumulative token usage statistics"""
        return {
            "total_tokens": self.total_tokens_used,
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens
        }
    
    def reset_usage_stats(self) -> None:
        """Reset token usage statistics"""
        self.total_tokens_used = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
    
    def _repair_json(self, json_str: str) -> Optional[str]:
        """
        Attempt to repair common JSON issues
        
        Args:
            json_str: Potentially malformed JSON string
            
        Returns:
            Repaired JSON string or None if repair not possible
        """
        import re
        
        try:
            repaired = json_str
            
            # Remove trailing commas before closing braces/brackets
            repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)
            
            # Remove comments (// and /* */)
            repaired = re.sub(r'//.*?$', '', repaired, flags=re.MULTILINE)
            repaired = re.sub(r'/\*.*?\*/', '', repaired, flags=re.DOTALL)
            
            # Try to fix single quotes to double quotes (only for keys, be conservative)
            # Only replace single quotes that look like key-value pairs
            repaired = re.sub(r"'([^']*)':\s*", r'"\1": ', repaired)
            
            # Fix unquoted keys (only if they're not already quoted)
            # Match word characters followed by colon, but not if already quoted
            # This is conservative - only fix obvious cases
            lines = repaired.split('\n')
            fixed_lines = []
            for line in lines:
                # Only fix lines that look like unquoted keys (word: value)
                # But skip if it's already quoted or looks like a value
                if re.match(r'^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*:', line) and '"' not in line[:20]:
                    # Add quotes around the key
                    line = re.sub(r'^(\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)', r'\1"\2"\3', line)
                fixed_lines.append(line)
            repaired = '\n'.join(fixed_lines)
            
            return repaired.strip()
        except Exception as e:
            self.logger.debug(f"JSON repair exception: {e}")
            return None


def get_llm_client(agent_name: Optional[str] = None) -> LLMClient:
    """
    Get a configured LLM client, optionally optimized for a specific agent
    
    Args:
        agent_name: Optional agent name to get agent-specific model
        
    Returns:
        LLMClient configured with appropriate model
    """
    from src.utils.config import config
    
    if agent_name:
        # Get agent-specific model (this will log the model name)
        model = config.get_agent_model(agent_name)
        logger.info(f"🤖 LLM Client for '{agent_name}': using model '{model}'")
    else:
        # Get default model
        llm_config = config.get_llm_config()
        model = llm_config.get('model', 'gpt-4o-mini')
        logger.info(f"🤖 LLM Client (default): using model '{model}'")
    
    return LLMClient(model=model)

