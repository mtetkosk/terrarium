"""Picker agent for bet selection"""

from typing import List, Optional, Dict, Any

from src.agents.base import BaseAgent
from src.data.models import BetType
from src.data.storage import Database
from src.prompts import PICKER_PROMPT
from src.utils.logging import get_logger
from src.utils.json_schemas import get_picker_schema

logger = get_logger("agents.picker")


class Picker(BaseAgent):
    """Picker agent for selecting bets"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Picker agent"""
        super().__init__("Picker", db, llm_client)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Picker"""
        return PICKER_PROMPT
    
    def process(
        self,
        researcher_output: Dict[str, Any],
        modeler_output: Dict[str, Any],
        historical_performance: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Select picks using LLM - exactly one pick per game
        
        Args:
            researcher_output: Output from Researcher
            modeler_output: Output from Modeler (predictions and edges)
            historical_performance: Historical performance data for learning
            
        Returns:
            LLM response with candidate picks (one per game)
        """
        if not self.is_enabled():
            self.log_warning("Picker agent is disabled")
            return {"candidate_picks": []}
        
        # Get games from researcher_output or modeler_output
        researcher_games = researcher_output.get("games", [])
        modeler_games = modeler_output.get("game_models", [])
        num_games = len(researcher_games) if researcher_games else len(modeler_games)
        
        # Batch processing: split games into smaller chunks to avoid huge prompts
        # Similar to Researcher (batch_size=5) and Modeler (batch_size=3)
        # Use larger batch size (10-15) since picks are simpler than research/modeling
        batch_size = self.config.get('picker_batch_size', 12)  # Process 12 games at a time
        
        if num_games <= batch_size:
            # Small number of games - process all at once
            result = self._process_batch(
                researcher_output, 
                modeler_output, 
                historical_performance,
                batch_num=1, 
                total_batches=1
            )
            picks = result.get("candidate_picks", [])
            
            # Filter out picks with extreme odds (same logic as batch processing)
            filtered_picks = []
            rejected_count = 0
            for pick in picks:
                odds_str = str(pick.get("odds", "-110"))
                try:
                    odds_int = int(odds_str.replace("+", "").replace("-", ""))
                    if odds_str.startswith("-") and odds_int > 500:
                        rejected_count += 1
                        self.log_warning(
                            f"Rejected pick with extreme odds {odds_str}: {pick.get('selection', 'Unknown')} "
                            f"(game_id: {pick.get('game_id', 'Unknown')})"
                        )
                        continue
                except (ValueError, AttributeError):
                    pass
                
                filtered_picks.append(pick)
            
            if rejected_count > 0:
                self.log_info(f"Filtered out {rejected_count} picks with extreme odds (worse than -500)")
            
            self.log_info(f"Selected {len(filtered_picks)} candidate picks (one per game)")
            
            return {
                "candidate_picks": filtered_picks,
                "overall_strategy_summary": result.get("overall_strategy_summary", [])
            }
        
        # Large number of games - batch process
        self.log_info(f"Selecting picks from {num_games} games using batch processing (batch size: {batch_size})")
        
        all_picks = []
        failed_batches = []
        processed_game_ids = set()
        
        # Create game_id to game_data mappings for efficient lookup
        researcher_by_id = {game.get("game_id"): game for game in researcher_games}
        modeler_by_id = {model.get("game_id"): model for model in modeler_games}
        
        # Use researcher_games as primary source, fallback to modeler_games if needed
        all_game_ids = set(researcher_by_id.keys()) | set(modeler_by_id.keys())
        game_ids_list = list(all_game_ids)
        
        total_batches = (len(game_ids_list) + batch_size - 1) // batch_size
        
        for i in range(0, len(game_ids_list), batch_size):
            batch_game_ids = game_ids_list[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            
            # Build batch data
            batch_researcher_games = [researcher_by_id[gid] for gid in batch_game_ids if gid in researcher_by_id]
            batch_modeler_games = [modeler_by_id[gid] for gid in batch_game_ids if gid in modeler_by_id]
            
            batch_researcher_output = {"games": batch_researcher_games}
            batch_modeler_output = {"game_models": batch_modeler_games}
            
            self.log_info(f"📦 Processing batch {batch_num}/{total_batches} ({len(batch_game_ids)} games)")
            
            # Process batch with retry
            batch_result = self._process_batch_with_retry(
                batch_researcher_output,
                batch_modeler_output,
                historical_performance,
                batch_num,
                max_retries=2
            )
            
            if batch_result and len(batch_result.get("candidate_picks", [])) > 0:
                batch_picks = batch_result.get("candidate_picks", [])
                all_picks.extend(batch_picks)
                # Track which games were processed
                for pick in batch_picks:
                    game_id = pick.get("game_id")
                    if game_id:
                        processed_game_ids.add(str(game_id))
                self.log_info(f"✅ Batch {batch_num} completed: {len(batch_picks)} picks")
            else:
                failed_batches.append(batch_num)
                self.log_warning(f"⚠️  Batch {batch_num} failed to generate picks")
        
        # Validate all games were processed
        if len(processed_game_ids) < len(game_ids_list):
            missing_count = len(game_ids_list) - len(processed_game_ids)
            self.log_warning(
                f"⚠️  Warning: Only {len(processed_game_ids)}/{len(game_ids_list)} games have picks. "
                f"{missing_count} games missing picks."
            )
        
        if failed_batches:
            self.log_warning(f"⚠️  {len(failed_batches)} batch(es) failed, {len(game_ids_list) - len(processed_game_ids)} games missing picks")
        else:
            self.log_info(f"✅ Successfully generated picks for all {len(processed_game_ids)} games")
        
        # Filter out picks with extreme odds
        filtered_picks = []
        rejected_count = 0
        for pick in all_picks:
            odds_str = str(pick.get("odds", "-110"))
            try:
                odds_int = int(odds_str.replace("+", "").replace("-", ""))
                if odds_str.startswith("-") and odds_int > 500:
                    rejected_count += 1
                    self.log_warning(
                        f"Rejected pick with extreme odds {odds_str}: {pick.get('selection', 'Unknown')} "
                        f"(game_id: {pick.get('game_id', 'Unknown')})"
                    )
                    continue
            except (ValueError, AttributeError):
                pass
            
            filtered_picks.append(pick)
        
        if rejected_count > 0:
            self.log_info(f"Filtered out {rejected_count} picks with extreme odds (worse than -500)")
        
        self.log_info(f"Selected {len(filtered_picks)} candidate picks for {num_games} games (one per game)")
        
        return {
            "candidate_picks": filtered_picks,
            "overall_strategy_summary": []  # Could aggregate strategy summaries if needed
        }
    
    def _process_batch_with_retry(
        self,
        researcher_output: Dict[str, Any],
        modeler_output: Dict[str, Any],
        historical_performance: Optional[Dict[str, Any]],
        batch_num: int,
        max_retries: int = 2
    ) -> Dict[str, Any]:
        """Process a batch of games with retry mechanism"""
        for attempt in range(max_retries + 1):
            try:
                batch_result = self._process_batch(
                    researcher_output,
                    modeler_output,
                    historical_performance,
                    batch_num,
                    1  # total_batches not needed for individual batch
                )
                if batch_result and len(batch_result.get("candidate_picks", [])) > 0:
                    return batch_result
                elif attempt < max_retries:
                    self.log_warning(f"Batch {batch_num} attempt {attempt + 1} returned no picks, retrying...")
            except Exception as e:
                if attempt < max_retries:
                    self.log_warning(f"Batch {batch_num} attempt {attempt + 1} failed: {e}, retrying...")
                else:
                    self.log_error(f"Batch {batch_num} failed after {max_retries + 1} attempts: {e}", exc_info=True)
        
        return {"candidate_picks": []}
    
    def _process_batch(
        self,
        researcher_output: Dict[str, Any],
        modeler_output: Dict[str, Any],
        historical_performance: Optional[Dict[str, Any]],
        batch_num: int,
        total_batches: int
    ) -> Dict[str, Any]:
        """Process a single batch of games - generates exactly one pick per game"""
        
        # Prepare input for LLM
        input_data = {
            "researcher_output": researcher_output,
            "modeler_output": modeler_output,
            "historical_performance": historical_performance or {}
        }
        
        num_games = len(researcher_output.get("games", []))
        if num_games == 0:
            num_games = len(modeler_output.get("game_models", []))
        
        historical_context = ""
        if historical_performance:
            hp = historical_performance
            historical_context = f"""

HISTORICAL PERFORMANCE (Learn from past results):
- Period: {hp.get('period', 'N/A')}
- Recent Performance: {hp.get('wins', 0)}W-{hp.get('losses', 0)}L-{hp.get('pushes', 0)}P ({hp.get('win_rate', 0):.1f}% win rate)
- ROI: {hp.get('roi', 0):.1f}%
- Total Profit: ${hp.get('total_profit', 0):.2f}
- Bet Type Performance: {hp.get('bet_type_performance', {})}
- Recent Recommendations: {hp.get('recent_recommendations', [])}

Use this historical data to:
- Learn which bet types have been most successful
- Adjust confidence levels based on recent accuracy
- Avoid patterns that led to losses
- Double down on strategies that have been profitable
"""

        user_prompt = f"""Please analyze the research data and model predictions to select betting opportunities.

CRITICAL REQUIREMENT: You MUST generate EXACTLY ONE pick for EVERY game provided. Do not skip any games. Do not generate multiple picks for the same game.

For each game:
- Generate exactly one pick (spread, total, or moneyline) based on model edge and research
- Choose the bet type with the best edge and reasoning for that specific game
- Include clear, detailed justification explaining why this pick was chosen
- The President will review ALL picks, assign units, and select the top 5 best bets

Focus on:
- Positive expected value (edge > 0) when possible
- Reasonable confidence levels
- Clear reasoning that combines model edge with contextual factors
{historical_context}
Provide clear, detailed justification for each pick that explains:
- Why this specific bet type was chosen (spread vs total vs moneyline)
- How the model edge supports this pick
- What contextual factors (injuries, recent form, matchups) influenced the decision
- How historical performance patterns informed this selection"""
        
        # Estimate prompt tokens
        import json
        from src.agents.base import _make_json_serializable
        
        serializable_data = _make_json_serializable(input_data)
        
        try:
            import tiktoken
            encoding = tiktoken.encoding_for_model(self.llm_client.model)
            formatted_prompt = f"""{user_prompt}

Input data:
{json.dumps(serializable_data, indent=2)}"""
            prompt_tokens_estimate = len(encoding.encode(self.system_prompt)) + len(encoding.encode(formatted_prompt))
        except (ImportError, KeyError, Exception):
            formatted_prompt = f"""{user_prompt}

Input data:
{json.dumps(serializable_data, indent=2)}"""
            prompt_tokens_estimate = (len(self.system_prompt) + len(formatted_prompt)) // 4
        
        # Determine context window size and calculate max_completion_tokens
        CONTEXT_WINDOW = 128000
        SAFETY_BUFFER = 2000
        remaining_space = CONTEXT_WINDOW - prompt_tokens_estimate - SAFETY_BUFFER
        
        if remaining_space < 16000:
            max_completion = 16000
            self.log_warning(
                f"⚠️  Very large prompt in batch {batch_num} ({prompt_tokens_estimate:,} tokens, {num_games} games), "
                f"only {remaining_space:,} tokens remaining. Setting max_completion_tokens to {max_completion:,}"
            )
        else:
            # Use remaining space, but cap based on game count
            if num_games > 40:
                max_completion = min(remaining_space, 50000)
            elif num_games > 20:
                max_completion = min(remaining_space, 40000)
            else:
                max_completion = min(remaining_space, 30000)
            
            if prompt_tokens_estimate > 80000:
                self.log_info(
                    f"Large prompt in batch {batch_num} ({prompt_tokens_estimate:,} tokens, {num_games} games), "
                    f"setting max_completion_tokens to {max_completion:,} (remaining space: {remaining_space:,})"
                )
        
        # Call LLM
        if hasattr(self.llm_client, 'client') and self.llm_client.client is not None:
            response = self._call_llm_with_max_tokens(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.5,
                parse_json=True,
                response_format=get_picker_schema(),
                max_tokens=max_completion
            )
        else:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.5,
                parse_json=True,
                response_format=get_picker_schema()
            )
        
        picks = response.get("candidate_picks", [])
        
        # Validate batch results
        if len(picks) < num_games:
            missing_count = num_games - len(picks)
            self.log_warning(
                f"⚠️  Batch {batch_num}: Only {len(picks)} picks generated for {num_games} games. "
                f"{missing_count} games missing picks."
            )
        
        response["candidate_picks"] = picks
        return response
    
    def _call_llm_with_max_tokens(
        self,
        user_prompt: str,
        input_data: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        parse_json: bool = True,
        response_format: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Call LLM with explicit max_tokens parameter
        
        This is a wrapper around call_llm that adds max_tokens support for large inputs
        """
        if not self.system_prompt:
            raise ValueError(f"Agent {self.name} has no system prompt defined")
        
        # Format user prompt with input data if provided
        from src.agents.base import _make_json_serializable
        import json
        
        if input_data:
            serializable_data = _make_json_serializable(input_data)
            formatted_prompt = f"""{user_prompt}

Input data:
{json.dumps(serializable_data, indent=2)}"""
        else:
            formatted_prompt = user_prompt
        
        self.logger.debug(f"Calling LLM for {self.name} with max_tokens={max_tokens}")
        
        # Get usage stats before call
        usage_before = self.llm_client.get_usage_stats()
        
        # Build messages
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": formatted_prompt}
        ]
        
        # Prepare kwargs for OpenAI API
        kwargs = {
            "model": self.llm_client.model,
            "messages": messages,
        }
        
        # Only add temperature if model is not gpt-5 (gpt-5 models don't support temperature)
        if not self.llm_client.model.startswith("gpt-5"):
            kwargs["temperature"] = temperature
        
        # Set max_tokens or max_completion_tokens based on model
        if max_tokens:
            if self.llm_client.model.startswith("gpt-5"):
                kwargs["max_completion_tokens"] = max_tokens
            else:
                kwargs["max_tokens"] = max_tokens
        
        # Add response_format if provided (structured output)
        if response_format:
            kwargs["response_format"] = response_format
        
        try:
            response = self.llm_client.client.chat.completions.create(**kwargs)
        except Exception as api_error:
            error_msg = str(api_error)
            # Check if error is due to unsupported response_format
            if "response_format" in error_msg.lower() or "structured output" in error_msg.lower():
                self.logger.warning(
                    f"Model {self.llm_client.model} may not support structured output. "
                    f"Falling back to regular JSON parsing. Error: {error_msg}"
                )
                # Retry without response_format
                if "response_format" in kwargs:
                    kwargs.pop("response_format", None)
                    response = self.llm_client.client.chat.completions.create(**kwargs)
            else:
                raise
        
        # Extract and log token usage
        usage = response.usage
        if usage:
            prompt_tokens = usage.prompt_tokens or 0
            completion_tokens = usage.completion_tokens or 0
            total_tokens = usage.total_tokens or 0
            
            # Track totals
            self.llm_client.total_prompt_tokens += prompt_tokens
            self.llm_client.total_completion_tokens += completion_tokens
            self.llm_client.total_tokens_used += total_tokens
            
            # Log token usage
            self.logger.info(
                f"📊 Token usage ({self.llm_client.model}): "
                f"Prompt: {prompt_tokens:,} | "
                f"Completion: {completion_tokens:,} | "
                f"Total: {total_tokens:,}"
            )
        
        # Get usage stats after call and log delta
        usage_after = self.llm_client.get_usage_stats()
        tokens_used = usage_after["total_tokens"] - usage_before["total_tokens"]
        prompt_tokens_delta = usage_after["prompt_tokens"] - usage_before["prompt_tokens"]
        completion_tokens_delta = usage_after["completion_tokens"] - usage_before["completion_tokens"]
        
        if tokens_used > 0:
            self.logger.info(
                f"💰 {self.name} token usage: "
                f"{tokens_used:,} total ({prompt_tokens_delta:,} prompt + {completion_tokens_delta:,} completion)"
            )
        
        # Check for truncation
        choice = response.choices[0]
        finish_reason = choice.finish_reason
        
        if finish_reason == "length":
            current_max = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens", "unknown")
            self.logger.error("⚠️  Response was TRUNCATED due to max_tokens limit!")
            self.logger.error(f"Current max_tokens/max_completion_tokens: {current_max}")
            self.logger.error(f"Prompt tokens: {usage.prompt_tokens if usage else 'unknown'}")
            self.logger.error(f"Completion tokens used: {usage.completion_tokens if usage else 'unknown'}")
            self.logger.error("⚠️  Some picks may be missing - response was truncated before completion")
        
        # Handle response
        message = response.choices[0].message
        content = message.content
        
        if parse_json:
            try:
                parsed = json.loads(content)
                # Validate that we have candidate_picks
                if "candidate_picks" not in parsed:
                    self.logger.warning("LLM response missing 'candidate_picks' field")
                    return {"candidate_picks": []}
                
                # Check if response was truncated and warn if picks are missing
                picks = parsed.get("candidate_picks", [])
                if finish_reason == "length" and len(input_data.get("researcher_output", {}).get("games", [])) > len(picks):
                    expected_games = len(input_data.get("researcher_output", {}).get("games", []))
                    self.logger.error(
                        f"⚠️  TRUNCATION DETECTED: Expected picks for {expected_games} games, "
                        f"but only received {len(picks)} picks. Response was truncated."
                    )
                
                return parsed
            except json.JSONDecodeError as e:
                self.logger.warning(f"JSON parsing error: {e}")
                self.logger.debug(f"Response content (first 500 chars): {content[:500] if content else 'None'}")
                
                # Try to extract JSON from markdown code blocks
                if content and "```json" in content:
                    json_start = content.find("```json") + 7
                    json_end = content.find("```", json_start)
                    if json_end > json_start:
                        json_str = content[json_start:json_end].strip()
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            pass
                
                return {
                    "candidate_picks": [],
                    "parse_error": str(e),
                    "raw_response": content[:500] if content else None
                }
        else:
            return {"raw_response": content}
    
