"""LLM client utilities for OpenAI and Google Gemini API"""

import json
import os
from typing import Optional, Dict, Any, List, Union

# Try to import OpenAI
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Try to import Gemini
try:
    import google.generativeai as genai
    from google.generativeai.types import HarmCategory, HarmBlockThreshold
    from google.api_core import retry
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

from src.utils.logging import get_logger
from src.prompts import generic_agent_user_prompt

logger = get_logger("utils.llm")


class LLMClient:
    """Client for OpenAI and Google Gemini API calls"""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini", provider: Optional[str] = None):
        """
        Initialize LLM client
        
        Args:
            api_key: API key (defaults to OPENAI_API_KEY or GEMINI_API_KEY env var based on provider)
            model: Model to use (default: gpt-4o-mini for OpenAI, gemini-3-flash for Gemini)
            provider: "openai" or "gemini" (auto-detected from model name if not provided)
        """
        # Auto-detect provider from model name if not specified
        if provider is None:
            if model.startswith("gpt-") or model.startswith("o1-") or model.startswith("o3-"):
                provider = "openai"
            elif model.startswith("gemini-"):
                provider = "gemini"
            else:
                # Default to OpenAI if ambiguous
                provider = "openai"
                logger.warning(f"Could not auto-detect provider for model '{model}', defaulting to OpenAI")
        
        self.provider = provider.lower()
        self.model = model
        
        if self.provider == "openai":
            if not OPENAI_AVAILABLE:
                raise ImportError("OpenAI package not installed. Install with: pip install openai")
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")
            if not self.api_key:
                raise ValueError("OpenAI API key required. Set OPENAI_API_KEY env var or pass api_key parameter.")
            self.client = openai.OpenAI(api_key=self.api_key)
        elif self.provider == "gemini":
            if not GEMINI_AVAILABLE:
                raise ImportError("Google Generative AI package not installed. Install with: pip install google-generativeai")
            self.api_key = api_key or os.getenv("GEMINI_API_KEY")
            if not self.api_key:
                raise ValueError("Gemini API key required. Set GEMINI_API_KEY env var or pass api_key parameter.")
            genai.configure(api_key=self.api_key)
            # Safety settings - completely disabled for content generation
            self.safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
        else:
            raise ValueError(f"Unknown provider: {provider}. Must be 'openai' or 'gemini'")
        
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
            tools: Optional list of tools
            tool_choice: Optional tool choice
            
        Returns:
            Parsed response (JSON dict if parse_json=True, else raw text)
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        return self.call_chat(
            messages=messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            parse_json=parse_json,
            tools=tools
        )

    def call_chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        parse_json: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make LLM Chat API call (supports both OpenAI and Gemini)
        """
        if self.provider == "openai":
            return self._call_openai_chat(
                messages=messages,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
                parse_json=parse_json,
                tools=tools,
                **kwargs
            )
        elif self.provider == "gemini":
            return self._call_gemini_chat(
                messages=messages,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
                parse_json=parse_json,
                tools=tools,
                **kwargs
            )
        else:
            raise ValueError(f"Unknown provider: {self.provider}")
    
    def _call_openai_chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        parse_json: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Make OpenAI Chat API call"""
        try:
            # Prepare request parameters
            request_params = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
            }
            
            # GPT-5.x models use max_completion_tokens instead of max_tokens
            if max_tokens:
                if self.model.startswith("gpt-5") or self.model.startswith("o1-") or self.model.startswith("o3-"):
                    request_params["max_completion_tokens"] = max_tokens
                else:
                    request_params["max_tokens"] = max_tokens
            
            # Handle response format (JSON mode)
            if response_format:
                request_params["response_format"] = {"type": "json_object"}
                # Also add instruction to prompt if not already there
                if messages and messages[-1].get("role") == "user":
                    user_msg = messages[-1]["content"]
                    if "json" not in user_msg.lower():
                        messages[-1]["content"] = f"{user_msg}\n\nRespond with valid JSON only."
            
            # Handle tools
            if tools:
                request_params["tools"] = tools
                if "tool_choice" in kwargs:
                    request_params["tool_choice"] = kwargs["tool_choice"]
            
            self.logger.debug(f"Calling OpenAI {self.model} with {len(messages)} messages")
            
            response = self.client.chat.completions.create(**request_params)
            
            # Extract usage
            if response.usage:
                prompt_tokens = response.usage.prompt_tokens
                completion_tokens = response.usage.completion_tokens
                total_tokens = response.usage.total_tokens
                
                self.total_prompt_tokens += prompt_tokens
                self.total_completion_tokens += completion_tokens
                self.total_tokens_used += total_tokens
                
                self.logger.info(
                    f"📊 Token usage ({self.model}): "
                    f"Prompt: {prompt_tokens:,} | "
                    f"Completion: {completion_tokens:,} | "
                    f"Total: {total_tokens:,}"
                )
            
            # Handle response
            choice = response.choices[0]
            
            # Check for tool calls
            if choice.message.tool_calls:
                tool_calls = []
                for tc in choice.message.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    })
                return {
                    "tool_calls": tool_calls,
                    "content": None,
                }
            
            content = choice.message.content or ""
            
            # Parse JSON if requested
            if parse_json and content:
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    # Try to extract JSON from markdown code blocks
                    import re
                    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                    if json_match:
                        try:
                            return json.loads(json_match.group(1))
                        except json.JSONDecodeError:
                            pass
                    
                    # Try to repair JSON
                    repaired = self._repair_json(content)
                    if repaired:
                        try:
                            return json.loads(repaired)
                        except json.JSONDecodeError:
                            pass
                    
                    snippet = (content[:500] + "... [truncated]") if len(content) > 500 else content
                    self.logger.warning(f"JSON parsing error: {e} | Raw content snippet: {snippet}")
                    return {"raw_response": content, "parse_error": str(e)}
            
            return {"raw_response": content}
            
        except Exception as e:
            self.logger.error(f"Error calling OpenAI: {e}", exc_info=True)
            raise
    
    def _call_gemini_chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        parse_json: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Make Gemini Chat API call"""
        try:
            # Configure the model
            generation_config = genai.types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            
            schema_conversion_failed = False
            if response_format:
                # Enable JSON mode at the API level. This is more robust than
                # relying on prompt instructions alone.
                generation_config.response_mime_type = "application/json"
                # Convert JSON Schema dict to Gemini's Schema proto format
                if isinstance(response_format, dict):
                    try:
                        # Convert the JSON Schema dict to Gemini's Schema proto
                        # The SDK expects a Schema proto object, not a plain dict
                        schema_proto = self._json_schema_to_gemini_schema(response_format)
                        generation_config.response_schema = schema_proto
                        self.logger.debug(f"Successfully set JSON schema for {self.model}")
                    except Exception as e:
                        self.logger.error(
                            f"Failed to convert response_format to Schema proto: {e}. "
                            f"Schema dict keys: {list(response_format.keys())}. "
                            f"Disabling JSON mode - will parse JSON from text response instead."
                        )
                        # If schema conversion fails, disable JSON mode entirely
                        # We'll parse JSON from the text response instead
                        generation_config.response_mime_type = None
                        generation_config.response_schema = None
                        schema_conversion_failed = True
            
            # Convert tools if provided
            gemini_tools = None
            if tools:
                gemini_tools = self._convert_tools(tools)

            # Extract system prompt if present (Gemini handles it separately in Model init)
            system_instruction = None
            chat_history = []
            
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content")
                
                if role == "system":
                    # Concatenate multiple system prompts if needed
                    if system_instruction:
                        system_instruction += "\n\n" + str(content)
                    else:
                        system_instruction = str(content)
                elif role == "user":
                    chat_history.append({"role": "user", "parts": [content]})
                elif role == "assistant":
                    parts = []
                    if content:
                        parts.append(content)
                    if msg.get("tool_calls"):
                        for tc in msg["tool_calls"]:
                            parts.append(
                                genai.protos.Part(
                                    function_call=genai.protos.FunctionCall(
                                        name=tc["function"]["name"],
                                        args=json.loads(tc["function"]["arguments"])
                                    )
                                )
                            )
                    chat_history.append({"role": "model", "parts": parts})
                elif role == "tool":
                    # Gemini expects function_response to follow function_call
                    chat_history.append({
                        "role": "function", 
                        "parts": [
                            genai.protos.Part(
                                function_response=genai.protos.FunctionResponse(
                                    name=msg.get("name"),
                                    response={"result": msg.get("content")}
                                )
                            )
                        ]
                    })

            # Create model
            model = genai.GenerativeModel(
                model_name=self.model,
                system_instruction=system_instruction,
                tools=gemini_tools,
                safety_settings=self.safety_settings
            )

            self.logger.debug(f"Calling {self.model} with {len(messages)} messages")
            
            gemini_contents = self._convert_to_gemini_contents(messages)
            
            response = model.generate_content(
                contents=gemini_contents,
                generation_config=generation_config
            )
            
            # Extract usage
            if response.usage_metadata:
                prompt_tokens = response.usage_metadata.prompt_token_count
                completion_tokens = response.usage_metadata.candidates_token_count
                total_tokens = response.usage_metadata.total_token_count
                
                self.total_prompt_tokens += prompt_tokens
                self.total_completion_tokens += completion_tokens
                self.total_tokens_used += total_tokens
                
                self.logger.info(
                    f"📊 Token usage ({self.model}): "
                    f"Prompt: {prompt_tokens:,} | "
                    f"Completion: {completion_tokens:,} | "
                    f"Total: {total_tokens:,}"
                )

            # Handle response - check for safety blocks first
            try:
                if not response.candidates:
                    # Check for safety filter blocks
                    safety_ratings = getattr(response, 'prompt_feedback', None)
                    if safety_ratings:
                        block_reason = getattr(safety_ratings, 'block_reason', None)
                        if block_reason:
                            self.logger.error(
                                f"Gemini blocked response due to safety filter: {block_reason}. "
                                f"Model: {self.model}"
                            )
                            return {
                                "raw_response": "",
                                "error": f"Safety filter blocked: {block_reason}"
                            }
                    
                    # No candidates and no explicit block reason
                    self.logger.error(
                        f"Gemini returned no candidates. Model: {self.model}. "
                        f"Check safety settings and prompt content."
                    )
                    return {"raw_response": "", "error": "No candidates returned"}
                
                # Check if candidate was blocked
                candidate = response.candidates[0]
                finish_reason = getattr(candidate, 'finish_reason', None)
                # Handle both enum and string representations
                finish_reason_str = str(finish_reason) if finish_reason else "None"
                
                # Log finish_reason for debugging
                if finish_reason:
                    self.logger.debug(f"Gemini finish_reason: {finish_reason_str} (type: {type(finish_reason)})")
                
                # Get safety ratings first
                safety_ratings = getattr(candidate, 'safety_ratings', [])
                
                # Check finish_reason - 2 typically means SAFETY block in Gemini enums
                # Also check if any safety ratings are blocked
                is_safety_block = False
                if finish_reason:
                    # Check if finish_reason indicates SAFETY (could be enum value 2, or string "SAFETY")
                    if (finish_reason_str == '2' or 
                        (hasattr(finish_reason, 'value') and finish_reason.value == 2) or
                        (hasattr(finish_reason, 'name') and finish_reason.name == 'SAFETY') or
                        'SAFETY' in finish_reason_str):
                        is_safety_block = True
                
                # Check safety ratings for blocked status
                blocked_ratings = []
                for rating in safety_ratings:
                    blocked = getattr(rating, 'blocked', False)
                    if blocked:
                        cat_name = getattr(rating.category, 'name', str(rating.category)) if hasattr(rating, 'category') else 'UNKNOWN'
                        prob_name = getattr(rating.probability, 'name', str(rating.probability)) if hasattr(rating, 'probability') else 'UNKNOWN'
                        blocked_ratings.append(f"{cat_name}: {prob_name}")
                
                # Only treat as safety block if we actually have blocked ratings
                # finish_reason 2 might not always mean SAFETY when safety filters are disabled
                # Since all safety settings are BLOCK_NONE, we should ignore finish_reason 2 if no ratings are blocked
                if blocked_ratings:
                    # Log detailed safety rating info
                    rating_details = []
                    for rating in safety_ratings:
                        cat = getattr(rating.category, 'name', str(rating.category)) if hasattr(rating, 'category') else 'UNKNOWN'
                        prob = getattr(rating.probability, 'name', str(rating.probability)) if hasattr(rating, 'probability') else 'UNKNOWN'
                        blocked = getattr(rating, 'blocked', False)
                        rating_details.append(f"{cat}: {prob} (blocked={blocked})")
                    
                    self.logger.error(
                        f"Gemini blocked candidate due to safety ratings: {blocked_ratings}. "
                        f"Finish reason: {finish_reason_str}. All ratings: {rating_details}. Model: {self.model}"
                    )
                    return {
                        "raw_response": "",
                        "error": f"Safety filter blocked: {', '.join(blocked_ratings)}"
                    }
                elif is_safety_block and not blocked_ratings:
                    # finish_reason suggests SAFETY but no ratings are blocked and safety filters are disabled
                    # This is likely a false positive - ignore it and try to continue
                    self.logger.warning(
                        f"Gemini candidate has finish_reason {finish_reason_str} (possibly SAFETY) but no blocked ratings found. "
                        f"Safety filters are all set to BLOCK_NONE. Ignoring finish_reason and attempting to extract content."
                    )
                    # Don't return error - continue to check for content
                
                # Check for content
                if not hasattr(candidate, 'content') or not candidate.content:
                    # Log detailed candidate info for debugging
                    candidate_attrs = {
                        "finish_reason": finish_reason_str,
                        "has_content": hasattr(candidate, 'content'),
                        "safety_ratings_count": len(safety_ratings),
                        "safety_ratings": [str(r) for r in safety_ratings],
                        "index": getattr(candidate, 'index', None),
                    }
                    self.logger.error(
                        f"Gemini candidate has no content attribute. Candidate info: {candidate_attrs}. "
                        f"Model: {self.model}. This may indicate a blocking issue."
                    )
                    return {"raw_response": "", "error": f"No content in candidate (finish_reason: {finish_reason_str})"}
                
                parts = candidate.content.parts
                if not parts:
                    # Log detailed candidate info for debugging
                    candidate_info = {
                        "finish_reason": finish_reason_str,
                        "has_content": True,
                        "content_type": type(candidate.content).__name__,
                        "content_attrs": dir(candidate.content) if hasattr(candidate, 'content') else [],
                        "safety_ratings": [str(r) for r in getattr(candidate, 'safety_ratings', [])],
                    }
                    self.logger.error(
                        f"Gemini candidate has no content parts. This usually means the response was blocked. "
                        f"Candidate info: {candidate_info}. Model: {self.model}. "
                        f"Try checking safety settings or prompt content."
                    )
                    return {"raw_response": "", "error": f"No content parts in candidate (finish_reason: {finish_reason_str})"}
                first_part = parts[0]
            except (IndexError, AttributeError) as e:
                self.logger.error(f"Error accessing response candidates: {e}")
                return {"raw_response": "", "error": f"No candidates returned: {str(e)}"}

            # Check for function calls (tools)
            if getattr(first_part, "function_call", None):
                fn_call = first_part.function_call
                # Return tool calls format matching OpenAI
                return {
                    "tool_calls": [
                        {
                            "id": "call_" + fn_call.name,
                            "type": "function",
                            "function": {
                                "name": fn_call.name,
                                "arguments": json.dumps(dict(fn_call.args)),
                            },
                        }
                    ],
                    "content": None,
                }

            # Concatenate all text parts – in JSON mode, the JSON can be split
            # across multiple parts, so using only the first one would truncate
            # the payload (e.g. just "{").
            text_chunks = [
                getattr(p, "text", "")
                for p in parts
                if getattr(p, "text", None)
            ]
            content = "".join(text_chunks)
            
            # Log full response for debugging when JSON mode is enabled
            if response_format and parse_json:
                self.logger.debug(
                    f"Full response content ({len(content)} chars, {len(parts)} parts): {content[:1000]}"
                )
            
            # If schema conversion failed, try to extract JSON from markdown code blocks
            if schema_conversion_failed and parse_json and content:
                # Try to extract JSON from markdown code blocks if present
                import re
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if json_match:
                    content = json_match.group(1)
                    self.logger.debug("Extracted JSON from markdown code block")
            
            if parse_json:
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    # Try to repair common JSON issues regardless of response_format,
                    # but never raise – instead, return the raw content so callers
                    # can decide how to handle fallback behavior.
                    # Log a short snippet of the raw content to help debugging
                    snippet = (content[:500] + "... [truncated]") if len(content) > 500 else content
                    self.logger.warning(f"JSON parsing error: {e} | Raw content snippet: {snippet}")
                    repaired = self._repair_json(content)
                    if repaired:
                        try:
                            return json.loads(repaired)
                        except json.JSONDecodeError:
                            # Fall through to returning raw response with parse error
                            pass
                    return {"raw_response": content, "parse_error": str(e)}
            
            return {"raw_response": content}

        except Exception as e:
            self.logger.error(f"Error calling Gemini: {e}", exc_info=True)
            raise

    def _convert_to_gemini_contents(self, messages: List[Dict[str, Any]]) -> List[Any]:
        """Convert OpenAI message format to Gemini content format"""
        contents = []
        
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            
            if role == "system":
                continue
                
            if role == "user":
                contents.append({"role": "user", "parts": [content]})
            
            elif role == "assistant":
                parts = []
                if content:
                    parts.append(content)
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        parts.append(
                            genai.protos.Part(
                                function_call=genai.protos.FunctionCall(
                                    name=tc["function"]["name"],
                                    args=json.loads(tc["function"]["arguments"])
                                )
                            )
                        )
                contents.append({"role": "model", "parts": parts})
                
            elif role == "tool":
                contents.append({
                    "role": "function",
                    "parts": [
                        genai.protos.Part(
                            function_response=genai.protos.FunctionResponse(
                                name=msg.get("name"),
                                response={"result": content} 
                            )
                        )
                    ]
                })
        
        return contents

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Any]:
        """Convert OpenAI tool definitions to Gemini tool definitions"""
        gemini_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                fn = tool["function"]
                # Normalize JSON Schema-style parameters into the format expected by
                # Gemini's proto-based Schema message.
                params = fn.get("parameters") or {}
                if isinstance(params, dict):
                    params = self._normalize_gemini_schema(params)

                gemini_tools.append(
                    genai.protos.Tool(
                        function_declarations=[
                            genai.protos.FunctionDeclaration(
                                name=fn["name"],
                                description=fn.get("description", ""),
                                parameters=params,
                            )
                        ]
                    )
                )
        
        return gemini_tools

    def _normalize_gemini_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert an OpenAI-style JSON Schema dict into the shape expected by
        Gemini's Schema proto.

        Key differences:
        - Field is named 'type_' in the proto, not 'type'
        - Enum values are UPPERCASE (e.g. 'OBJECT', 'STRING', 'INTEGER', 'ARRAY')
        """
        if not isinstance(schema, dict):
            return schema  # type: ignore[return-value]
        
        def _convert(node: Any) -> Any:
            if not isinstance(node, dict):
                return node
            
            converted: Dict[str, Any] = {}
            for key, value in node.items():
                # Drop fields that Gemini's Schema proto does not support
                if key in {"default"}:
                    continue
                if key == "type":
                    # Map JSON Schema string types to Gemini enum names
                    if isinstance(value, str):
                        # Common JSON schema values are lowercase; Gemini expects enum names
                        enum_value = value.upper()
                        converted["type_"] = enum_value
                    else:
                        converted["type_"] = value
                elif key == "properties" and isinstance(value, dict):
                    # Recurse into object properties
                    converted["properties"] = {
                        prop_name: _convert(prop_schema)
                        for prop_name, prop_schema in value.items()
                    }
                elif key == "items":
                    # Recurse into array item schema
                    converted["items"] = _convert(value)
                else:
                    converted[key] = _convert(value) if isinstance(value, dict) else value
            return converted
        
        return _convert(schema)

    def call_agent(
        self,
        agent_name: str,
        system_prompt: str,
        input_data: Dict[str, Any],
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """Call an agent with structured input/output"""
        user_prompt = generic_agent_user_prompt(input_data)
        
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
    
    def _json_schema_to_gemini_schema(self, schema_dict: Dict[str, Any]) -> Any:
        """
        Convert a JSON Schema dict to Gemini's Schema proto format.
        
        The Gemini Python SDK's GenerationConfig.response_schema expects a Schema proto.
        We need to convert the JSON Schema format (type, properties) to Gemini's proto
        format (type_, enum values like OBJECT, STRING, etc.).
        """
        try:
            # Normalize the schema to proto format (type -> type_, "object" -> "OBJECT", etc.)
            normalized = self._normalize_gemini_schema(schema_dict)
            # Create Schema proto object
            return genai.protos.Schema(**normalized)
        except Exception as e:
            self.logger.warning(
                f"Failed to create Schema proto from dict: {e}. "
                f"Schema dict: {json.dumps(schema_dict, indent=2)[:500]}"
            )
            raise
    
    def _repair_json(self, json_str: str) -> Optional[str]:
        """Attempt to repair common JSON issues"""
        import re
        try:
            repaired = json_str
            # Remove markdown code blocks
            if "```json" in repaired:
                repaired = repaired.split("```json")[1].split("```")[0]
            elif "```" in repaired:
                repaired = repaired.split("```")[1].split("```")[0]
                
            repaired = repaired.strip()
            
            # Remove trailing commas
            repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)
            return repaired
        except Exception:
            return None


def get_llm_client(agent_name: Optional[str] = None) -> LLMClient:
    """Get a configured LLM client"""
    from src.utils.config import config
    
    if agent_name:
        model = config.get_agent_model(agent_name)
        logger.debug(f"🤖 LLM Client for '{agent_name}': using model '{model}'")
    else:
        model = config.get('llm.model', 'gpt-4o-mini')
        logger.debug(f"🤖 LLM Client (default): using model '{model}'")
    
    # Auto-detect provider from model name
    provider = None
    if model.startswith("gpt-") or model.startswith("o1-") or model.startswith("o3-"):
        provider = "openai"
    elif model.startswith("gemini-"):
        provider = "gemini"
    else:
        # Default to OpenAI
        provider = "openai"
        logger.debug(f"🤖 Auto-detected provider 'openai' for model '{model}'")
    
    return LLMClient(model=model, provider=provider)
